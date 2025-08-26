# 📦 On-Chain Variable Storage for Algorand

This repository explores a design pattern for managing **large variable storage** on Algorand smart contracts, beyond the 32 KB per-box limit.  

The approach is inspired by **stack vs. heap memory management** in traditional programming.  

---

## 🔧 How it Works

We introduce two abstractions:

- **META** → Index that tracks *how many 32 KB segments exist* for a logical key (and optionally total length). Concretely in code: `metadata: BoxMap<UInt64, UInt64>` with `metadata[base] = seg_count`.
- **HEAP** → The actual bytes, split across **32 KB** boxes. Concretely in code: `memory: BoxMap<UInt64, String>`; each segment lives in its own box.

### Key Derivation (deterministic)

- **Base key** (8 bytes → UInt64):
  - On-chain: `base = btoi(sha256(raw_key)[:8])`
  - Off-chain (Python):

    ```py
    import hashlib
    def base_u64(raw_key: str) -> int:
        return int.from_bytes(hashlib.sha256(raw_key.encode()).digest()[:8], 'big')
    ```

- **Segment key** for segment **i ≥ 1**:
  - `seg_key(i) = base + i` (as UInt64)
  - Box name is the 8‑byte big‑endian form of that UInt64 when referenced off‑chain.

> Collision risk is negligible at our scale (~1/2^64 birthday bound).

### Constraints

- `32_768` bytes (Algorand per‑box cap)
- App call payloads are kept **≤ ~2 KB**, so each call appends at most once and may **spill** into the next segment at most once.

### Write / Append Flow (≤ 2 KB per call)

1. Compute `base` from `raw_key`.
2. Read `seg_count = metadata.get(base, 0)`.
3. Let **current segment** key be:
   - `cur_key = base + 1` if `seg_count == 0`, else `cur_key = base + seg_count`.
4. Compute `cur_len = memory.length(cur_key)` (0 if the box doesn’t exist yet), `space_left = MAX_BOX - cur_len`.
5. If `len(value) ≤ space_left` → **append** into `cur_key` using `resize + splice`.
6. Else (**boundary spill**) → write the head to `cur_key`, write the tail (remaining bytes) at offset `0` into **next segment** `next_key = base + seg_count + 1`, then set `metadata[base] = seg_count + 1`.

This contract never loops over many boxes; a single call can touch **at most two segments** (current + next) which fits the 2 KB call budget.

### Read / Reconstruct Flow

- Off‑chain: read `N = metadata[base]` (0 means no data). For `i = 1..N`:
  - Box name = `(base + i)` → 8‑byte big‑endian
  - Concatenate each segment’s bytes in order to rebuild the value.
- On‑chain partial reads can use `box_extract`, but note the 4,096‑byte stack limit if attempting to assemble large buffers in AVM.

### Required Box References per Append Call

Include these in your app call when appending:

- **Metadata** box for `base` (8‑byte name)
- **Current segment**: `base + seg_count` *or* `base + 1` if this is the first write
- **Next segment**: `base + seg_count + 1` (only needed if you might cross a boundary; safe to always include)

Off‑chain helpers:

```py
import hashlib

def base_u64(raw_key: str) -> int:
    return int.from_bytes(hashlib.sha256(raw_key.encode()).digest()[:8], 'big')

def seg_box_name(raw_key: str, i: int) -> bytes:
    """i is 1-based segment index"""
    return (base_u64(raw_key) + i).to_bytes(8, 'big')
```

### Example: variable `"a"` storing a 100 KB JSON

1. Compute `base = btoi(sha256("a")[:8])`.
2. Split into ~32 KB segments: `a[1]`, `a[2]`, `a[3]`, `a[4]`.
3. Store as:
   - Box `base+1` → first 32 KB
   - Box `base+2` → next 32 KB
   - Box `base+3` → next 32 KB
   - Box `base+4` → last ~4 KB
4. Set `metadata[base] = 4`.
5. When appending later, the contract tries `base+4` first and spills into `base+5` if needed (then updates `metadata[base] = 5`).

### Notes & Considerations

- **Collisions**: 64‑bit space is ample for typical app usage; if you expect millions of distinct keys, you may add namespace prefixes to `raw_key` (e.g., `"user:"+id`).
- **Costs**: Storage cost is per‑box key+value bytes. Appends use `box_resize` + `box_splice`. Keeping calls ≤2 KB avoids exceeding AVM argument limits.
- **Deletion**: To delete a variable, delete all segments `base+1..base+N` and clear `metadata[base]`.
- **Indexing by path**: If you need random access to nested JSON fields on‑chain, add a secondary index (e.g., hashed field paths → (segment, offset, length)). This repo focuses on efficient append & linear read.
