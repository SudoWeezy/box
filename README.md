# 📦 On-Chain Variable Storage for Algorand

This repository explores a design pattern for managing **large variable storage** on Algorand smart contracts, beyond the 32 KB per-box limit.  


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

- **Collisions**: 64‑bit space is ample for typical app usage; we can consider to go lower for most cases
- **Costs**: Storage cost is per‑box key+value bytes. Appends use `box_resize` + `box_splice`. Keeping calls ≤2 KB avoids exceeding AVM argument limits.
- **Deletion**: To delete a variable, delete all segments `base+1..base+N` and clear `metadata[base]`.

> This repo focuses on efficient append & linear read.
> - **Indexing by path**: If you need random access to nested JSON fields on‑chain, you can add a secondary index (e.g., hashed field paths → (segment, offset, length)).

## 🧩 JSON Storage Mode (Flattened Fields)

In this mode there is **one entry per path**. You hash the **fully‑qualified JSON field path** (e.g., `user.emails[0]`) to derive a **64‑bit key**, and you use that same key for both the META entry and the HEAP entry — **no raw key prefix**.

- **META (index)**
  - **Box key**: `field_key = btoi(sha256(path)[:8])`
  - **Box value**: the path itself (UTF‑8), e.g. `"user.emails[0]"`
  - Purpose: allows discovery/enumeration of which paths exist without reading large values.

- **HEAP (data)**
  - **Box key**: same `field_key`
  - **Box value**: the **actual bytes** for that field (string bytes, JSON‑encoded scalar, binary, etc.). Must be ≤ **32_768** bytes.

There is **no base key** and **no +1/+2 increment** here; every flattened path is its own addressable record.

### Example

Given the following JSON:
```json
{
  "user": {
    "name": "Alice",
    "age": 42,
    "emails": ["a@example.com", "b@example.com"]
  }
}
```
Define **paths** (no raw key prefix):
- `user.name`
- `user.age`
- `user.emails[0]`
- `user.emails[1]`

For each path `p`:
1) Compute `k = btoi(sha256(p)[:8])`
2) Create **META** box named `k.to_bytes(8,'big')` with value = `p` (UTF‑8)
3) Create **HEAP** box named `k.to_bytes(8,'big')` with value = the field bytes
   - `user.name`  → bytes("Alice")
   - `user.age`   → 8‑byte big‑endian integer **or** bytes("42")
   - `user.emails[0]` → bytes("a@example.com")
   - `user.emails[1]` → bytes("b@example.com")

### Read / Update

- **Read**: given a known path `p`, compute `k = btoi(sha256(p)[:8])` and read the HEAP box `k`.
- **Enumerate**: scan META boxes (your app can namespace them, e.g., by keeping a small directory list) and read their UTF‑8 values to list available paths.
- **Update**: same key derivation; overwrite the HEAP value for `k`.

### Python Helpers (off‑chain)

```py
import hashlib, json
from typing import Any, Dict, List, Tuple

MAX_BOX = 32_768

def u64_sha256_8(s: str) -> int:
    return int.from_bytes(hashlib.sha256(s.encode()).digest()[:8], 'big')

def box_name_u64(x: int) -> bytes:
    return x.to_bytes(8, 'big')

# Flatten JSON into (path, value_bytes) WITHOUT any raw-key prefix

def flatten_paths(obj: Any, prefix: str = "") -> List[Tuple[str, bytes]]:
    out: List[Tuple[str, bytes]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            out.extend(flatten_paths(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{prefix}[{i}]"
            out.extend(flatten_paths(v, p))
    else:
        # store JSON scalar encoding to preserve type
        out.append((prefix, json.dumps(obj, ensure_ascii=False).encode('utf-8')))
    return out

# Build META and HEAP records where keys are identical (hash(path))
# Returns: meta: Dict[u64_key, bytes(path_utf8)], heap: Dict[u64_key, bytes(value)]

def build_flat_records(obj: Any):
    meta: Dict[int, bytes] = {}
    heap: Dict[int, bytes] = {}
    for path, value_bytes in flatten_paths(obj):
        k = u64_sha256_8(path)
        assert len(value_bytes) <= MAX_BOX, f"Value too large for one box: {path} has {len(value_bytes)} bytes"
        meta[k] = path.encode('utf-8')
        heap[k] = value_bytes
    return meta, heap
```

### Notes

- **Size limits**: Each field value must fit into **one box** (≤ 32 KB). If you need larger than 32 KB for a single field, use the append/segment model from earlier for that field only, and store a small pointer object in HEAP (e.g., `{ "seg_base": <u64>, "segments": N }`).
- **Schema choices**: META can be JSON, CBOR, or tight binary. JSON is simplest to debug; CBOR/binary is most compact.
- **Determinism**: Keys are derived by hashing the path string itself (e.g., 
`user.emails[0]`). Use unique, stable paths in your application schema to avoid collisions across logical variables.
- **Types**: You can store integers as 8‑byte big‑endian for efficient on‑chain use, or as JSON strings for flexibility.

> Warning: This approach cannot distinguish between different JSON documents that share the same field paths, since the key is derived solely from the path string.