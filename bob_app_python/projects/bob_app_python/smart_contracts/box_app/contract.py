from algopy import ARC4Contract, BoxMap, Global, String, Txn, UInt64, itxn, op
from algopy.arc4 import abimethod


class BoxApp(ARC4Contract):
    def __init__(self) -> None:
        self.memory = BoxMap(UInt64, String, key_prefix="")
        self.metadata = BoxMap(UInt64, UInt64, key_prefix="")

    @abimethod()
    def fill_box(self, raw_key: String, value: String, index: UInt64) -> None:
        value_bytes = value.bytes
        metadata_key = op.btoi(op.sha256(raw_key.bytes)[:8])
        key = metadata_key + 1
        len_value = value_bytes.length
        if len_value > 0:
            if key not in self.memory:
                self.memory[key] = value
                self.metadata[metadata_key] = UInt64(0)
            elif (len_value + (idx := self.memory.length(key))) <= UInt64(32768):
                self.memory.box(key).ref.resize(idx + len_value)
                self.memory.box(key).ref.splice(idx, len_value, value_bytes)
            else:
                pass  # TODO Handle concat between 2 box

    @abimethod()
    def delete_box(self, raw_key: String, index: UInt64) -> None:
        metadata_key = op.btoi(op.sha256(raw_key.bytes)[:8])
        key = metadata_key + 1
        del self.memory[key]  # Todo, loop through all key
        del self.metadata[metadata_key]

    @abimethod(allow_actions=["DeleteApplication"])
    def delete_application(
        self,
    ) -> None:  # Only allow the creator to delete the application
        assert (
            Txn.sender == Global.creator_address
        )  # Send all the unsold assets to the creator
        itxn.Payment(
            receiver=Global.creator_address,
            amount=0,
            close_remainder_to=Global.creator_address,
            fee=Global.min_txn_fee,
        ).submit()
        # Get back ALL the ALGO in the creatoraccount
