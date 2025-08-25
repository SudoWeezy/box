import hashlib
import logging
import re

import algokit_utils
import pytest
from algokit_utils import (
    AlgoAmount,
    AlgorandClient,
    AppCallMethodCallParams,
    CommonAppCallParams,
    PaymentParams,
    SigningAccount,
)

from smart_contracts.artifacts.box_app.box_app_client import (
    BoxAppClient,
    BoxAppFactory,
    DeleteBoxArgs,
    FillBoxArgs,
)

logger = logging.getLogger(__name__)


def btoi_sha256_8(raw_key: str) -> int:
    """Hash raw_key and return first 8 bytes as UInt64 (int)."""
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def int_to_bytes8(value: int) -> bytes:
    """Convert UInt64 int back into exactly 8 bytes (big endian)."""
    return value.to_bytes(8, "big", signed=False)


def get_min_balance_required(ac, method) -> int | None:
    """
    Simulates a transaction and extracts the minimum balance required if the transaction fails due to insufficient funds.

    :param ac: The application client instance.
    :param method: The method for the application call.
    :return: The minimum balance required if an insufficient balance error occurs, otherwise None.
    """
    try:
        ac.algorand.new_group().add_app_call_method_call(method).simulate()
        return 0  # If no error occurs, return None (no min balance issue)

    except Exception as e:
        error_message = str(e)
        print(error_message)
        # Extract the current balance and required minimum balance
        match = re.search(r"balance (\d+) below min (\d+)", error_message)
        if match:
            min_balance_required = int(match.group(2)) - int(match.group(1))
            print(min_balance_required)
            return min_balance_required  # Return the extracted min balance required

        return None


def box_exists(algorand_client: AlgorandClient, app_id: int, box_name: bytes) -> bool:
    try:
        # Algokit client exposes the raw algod at .client.algod
        algorand_client.client.algod.get_application_box_by_name(app_id, box_name)
        return True
    except Exception:
        return False


@pytest.fixture()
def deployer(algorand_client: AlgorandClient) -> SigningAccount:
    account = algorand_client.account.from_environment("DEPLOYER")
    algorand_client.account.ensure_funded_from_environment(
        account_to_fund=account.address, min_spending_balance=AlgoAmount.from_algo(10)
    )
    return account


@pytest.fixture()
def box_app_client(
    algorand_client: AlgorandClient, deployer: SigningAccount
) -> BoxAppClient:
    factory = algorand_client.client.get_typed_app_factory(
        BoxAppFactory, default_sender=deployer.address
    )

    client, _ = factory.deploy(
        on_schema_break=algokit_utils.OnSchemaBreak.AppendApp,
        on_update=algokit_utils.OnUpdate.AppendApp,
    )
    return client


def test_fill_box_less_than_32(
    box_app_client: BoxAppClient, deployer: SigningAccount
) -> None:
    # Arrange
    len_dummy_input = 32768 - 4768
    dummy_input = "a" * len_dummy_input
    raw_key = "test"
    chunk_size = 2000 - 100

    nb_calls = len_dummy_input // chunk_size
    last_call = len_dummy_input % chunk_size

    assert len_dummy_input <= 32768
    algorand = AlgorandClient.from_environment()

    # min_balance = get_min_balance_required()
    min_balance = 20000000
    composer = algorand.new_group()

    composer.add_payment(
        PaymentParams(
            receiver=box_app_client.app_address,
            amount=AlgoAmount(micro_algo=min_balance),
            sender=deployer.address,
            signer=deployer.signer,
        )
    )

    def fill_box(raw_key: str, value: int, index: int) -> AppCallMethodCallParams:
        meta_data_key = int_to_bytes8(btoi_sha256_8(raw_key=raw_key))
        key = int_to_bytes8(btoi_sha256_8(raw_key=raw_key) + 1)
        args = FillBoxArgs(raw_key=raw_key, value=value, index=index)
        param = CommonAppCallParams(
            box_references=[key, meta_data_key], signer=deployer.signer
        )
        return box_app_client.params.fill_box(args, param)

    # Appels avec les morceaux
    for i in range(nb_calls):
        chunk = dummy_input[i * chunk_size : (i + 1) * chunk_size]
        composer.add_app_call_method_call(fill_box(raw_key, chunk, i))

    # Dernier morceau s'il reste quelque chose
    if last_call > 0:
        chunk = dummy_input[nb_calls * chunk_size :]
        composer.add_app_call_method_call(fill_box(raw_key, chunk, nb_calls))

    composer.send()


def test_delete_box_removes_memory_and_metadata(
    box_app_client: BoxAppClient, deployer: SigningAccount
) -> None:
    algorand = AlgorandClient.from_environment()

    raw_key = "test"

    # Compute box names (8-byte big-endian) matching on-chain logic
    meta_u64 = btoi_sha256_8(raw_key)
    meta_name = int_to_bytes8(meta_u64)
    key_name = int_to_bytes8(meta_u64 + 1)

    # 2) Delete the box via ABI
    composer = algorand.new_group()
    box_memory = algorand.app.get_box_value(box_app_client.app_id, key_name)
    box_metadata = algorand.app.get_box_value(box_app_client.app_id, meta_name)
    del_params = CommonAppCallParams(
        box_references=[key_name, meta_name], signer=deployer.signer
    )
    nbcalls = (len(box_memory) + len(box_metadata)) // 2048 + 1

    for i in range(0, nbcalls):
        composer.add_app_call_method_call(
            box_app_client.params.delete_box(
                DeleteBoxArgs(raw_key=raw_key, index=i), del_params
            )
        )

    composer.send()

    # Verify both are gone
    assert not box_exists(algorand, box_app_client.app_id, key_name)
    assert not box_exists(algorand, box_app_client.app_id, meta_name)


# def test_says_hello(box_app_client: BoxAppClient) -> None:
#     result = box_app_client.send.hello(args=("World",))
#     assert result.abi_return == "Hello, World"


# def test_simulate_says_hello_with_correct_budget_consumed(
#     box_app_client: BoxAppClient,
# ) -> None:
#     result = (
#         box_app_client.new_group()
#         .hello(args=("World",))
#         .hello(args=("Jane",))
#         .simulate()
#     )
#     assert result.returns[0].value == "Hello, World"
#     assert result.returns[1].value == "Hello, Jane"
#     assert result.simulate_response["txn-groups"][0]["app-budget-consumed"] < 100
