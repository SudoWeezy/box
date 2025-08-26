from algopy import ARC4Contract, BoxMap, Global, String, Txn, UInt64, itxn, op
from algopy.arc4 import abimethod


class BoxApp(ARC4Contract):
    def __init__(self) -> None:
        self.memory = BoxMap(UInt64, String, key_prefix="")
        self.metadata = BoxMap(UInt64, UInt64, key_prefix="")

    @abimethod()
    def fill_box(self, raw_key: String, value: String, index: UInt64) -> None:
        vb = value.bytes
        lv = vb.length
        if lv == UInt64(0):
            return

        base = op.btoi(op.sha256(raw_key.bytes)[:8])
        seg_count = UInt64(1)
        if base in self.metadata:
            seg_count = self.metadata[base]
        else:
            self.metadata[base] = UInt64(1)

        # Determine current box key
        cur_key = base + seg_count

        if cur_key not in self.memory:
            # First write for this base
            if lv <= UInt64(32768):
                self.memory[cur_key] = value
                # created first segment
                self.metadata[base] = UInt64(2)
                return
            # Spill into two boxes: head into seg1, tail into seg2
            head_len = UInt64(32768)
            head = vb[0:head_len]
            tail = vb[head_len : lv - head_len]

            self.memory.box(cur_key).ref.resize(head.length)
            self.memory.box(cur_key).ref.splice(UInt64(0), head.length, head)
            next_key = base + UInt64(2)
            self.memory.box(next_key).ref.resize(tail.length)
            self.memory.box(next_key).ref.splice(UInt64(0), tail.length, tail)
            self.metadata[base] = UInt64(2)
            return

        # Append to existing current segment (seg_count >= 1)
        cur_len = self.memory.length(cur_key)
        space_left = UInt64(32768) - cur_len

        if lv <= space_left:
            # Fits in current segment
            self.memory.box(cur_key).ref.resize(cur_len + lv)
            self.memory.box(cur_key).ref.splice(cur_len, lv, vb)
            return

        # Spill tail to next segment
        head_len = space_left
        head = vb[0:head_len]
        tail = vb[head_len : lv - head_len]

        # Append head to current
        self.memory.box(cur_key).ref.resize(cur_len + head_len)
        self.memory.box(cur_key).ref.splice(cur_len, head_len, head)

        # Write tail to next segment start
        next_key = base + (seg_count + UInt64(1))
        if next_key not in self.memory:
            self.memory[next_key] = String("")

        self.memory.box(next_key).ref.resize(tail.length)
        self.memory.box(next_key).ref.splice(UInt64(0), tail.length, tail)

        # We just created/used the next segment
        self.metadata[base] = seg_count + UInt64(1)

    @abimethod()
    def delete_box(self, raw_key: String, index: UInt64) -> None:
        base = op.btoi(op.sha256(raw_key.bytes)[:8])

        # If no metadata entry, nothing to delete
        if base not in self.metadata:
            return

        seg_count = self.metadata[base]
        # Delete all segments base+1 .. base+seg_count (if present)
        i = UInt64(1)
        while i <= seg_count:
            seg_key = base + i
            if seg_key in self.memory:
                del self.memory[seg_key]
            i = i + UInt64(1)

        # Finally delete metadata record
        del self.metadata[base]

    @abimethod(allow_actions=["DeleteApplication"])
    def delete_application(
        self,
    ) -> None:  # Only allow the creator to delete the application
        assert Txn.sender == Global.creator_address
        itxn.Payment(
            receiver=Global.creator_address,
            amount=0,
            close_remainder_to=Global.creator_address,
            fee=Global.min_txn_fee,
        ).submit()
        # Get back ALL the ALGO in the creatoraccount
