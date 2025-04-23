"""
abstract: Tests worst case bytecode size scenarios using a factory contract approach.
    This test creates contracts with large bytecode using a factory pattern to avoid
    large pre-allocated state. The test verifies that clients can handle large contract
    deployments and code size checks correctly. Each contract has unique bytecode based on
    a hash of the storage value.
"""

import pytest

from ethereum_test_base_types import Address
from ethereum_test_forks import Fork
from ethereum_test_tools import (Alloc, Block, BlockchainTestFiller,
                                 Environment, Transaction)
from ethereum_test_tools.vm.opcode import Opcodes as Op
from ethereum_test_types.helpers import compute_create_address
from ethereum_test_vm.bytecode import Bytecode

REFERENCE_SPEC_GIT_PATH = "EIPS/eip-9999.md"
REFERENCE_SPEC_VERSION = "TBD"

MAX_CONTRACT_SIZE = 24 * 1024  # Maximum contract size (24KB)
CONTRACT_DEPLOYMENT_GAS = 5_000_000  # Gas estimate for deploying one 24KB contract
MAX_NUM_CONTRACT_CALLS = 10  # Fixed number of contracts

@pytest.fixture
def env(gas_limit=36_000_000) -> Environment:
    """Prepare the environment for all test cases."""
    return Environment(gas_limit=gas_limit)

@pytest.fixture
def factory_contract(pre: Alloc) -> Address:
    """Deploy the factory contract that creates large contracts with unique bytecode."""
    # Simplified factory contract that:
    # 1. Gets a unique seed from storage
    # 2. Creates a contract with repeated copies of the seed
    factory_code = (
        # Get and increment nonce
        Op.SLOAD(0) +          # [nonce]
        Op.PUSH1(1) +          # [1, nonce]
        Op.ADD +               # [nonce+1]
        Op.DUP1 +              # [nonce+1, nonce+1]
        Op.PUSH0 +             # [0, nonce+1, nonce+1]
        Op.SSTORE +            # [nonce+1] (stores incremented nonce)
        
        # Store nonce at memory position 2 (after first 2 bytes to not trigger EOF)
        Op.PUSH1(2) +          # [2, nonce+1]
        Op.MSTORE +            # []
        
        # Create contract with nonce after first 2 bytes
        Op.PUSH2(MAX_CONTRACT_SIZE) +  # [MAX_SIZE]
        Op.PUSH1(32) +         # [32, MAX_SIZE] (code starts at 0x20)
        Op.PUSH1(0) +          # [0, 32, MAX_SIZE] (no ETH sent)
        Op.CREATE +            # [new_contract_address]
        
        # Return the created address
        Op.PUSH1(0) +          # [0, new_contract_address]
        Op.MSTORE +            # []
        Op.PUSH1(20) +         # [20]
        Op.PUSH1(12) +         # [12, 20]
        Op.RETURN              # []
    )
    
    return pre.deploy_contract(code=factory_code)

@pytest.mark.zkevm
@pytest.mark.valid_from("Cancun")
@pytest.mark.parametrize(
    "num_called_contracts, gas_limit",
    [
        (2, 36_000_000),
        (5, 60_000_000),
        (10, 120_000_000),
    ],
)
def test_worst_bytecode(
    blockchain_test: BlockchainTestFiller,
    pre: Alloc,
    fork: Fork,
    env: Environment,
    factory_contract: Address,
    num_called_contracts: int,
    gas_limit: int,
):
    """Test a block execution calling contracts with large bytecode."""
    # Override the environment's gas limit
    env.gas_limit = gas_limit
    
    # Create transactions to deploy large contracts
    deploy_txs = []
    
    # Keep track of sender accounts for calculating contract addresses
    factory_callers = []
    
    # Create transactions for each contract deployment
    for _ in range(num_called_contracts):
        sender = pre.fund_eoa()
        factory_callers.append(sender)
        deploy_txs.append(
            Transaction(
                to=factory_contract,
                gas_limit=gas_limit,
                gas_price=10,
                sender=sender,
                data=[],
                value=0,
            )
        )
    
    # Calculate how many contracts we can deploy per block
    contracts_per_block = gas_limit // CONTRACT_DEPLOYMENT_GAS
    
    # Split deploy transactions into blocks
    blocks = []
    for i in range(0, len(deploy_txs), contracts_per_block):
        block_txs = deploy_txs[i:i + contracts_per_block]
        blocks.append(Block(txs=block_txs))
    
    # Calculate the addresses of the deployed contracts
    # The factory contract's nonce starts at 0 and increments with each CREATE
    contract_addresses = []
    for i in range(num_called_contracts):
        contract_address = compute_create_address(address=factory_contract, nonce=i)
        contract_addresses.append(contract_address)
    
    # Create attack contract with the actual contract addresses
    attack_code = Bytecode()
    
    for address in contract_addresses:
        attack_code += (
            Op.PUSH20(address) +     # [address]
            Op.EXTCODESIZE +         # [size]
            Op.POP                   # []
        )
    
    attack_code += Op.STOP  # Stop execution
    attack_contract = pre.deploy_contract(code=attack_code)
    
    # Final transaction to execute the attack
    attack_tx = Transaction(
        to=attack_contract,
        gas_limit=gas_limit,
        gas_price=10,
        sender=pre.fund_eoa(),
        data=[],
        value=0,
    )
    
    # Add final block with attack transaction
    blocks.append(Block(txs=[attack_tx]))
    
    blockchain_test(
        env=env,
        pre=pre,
        post={},
        blocks=blocks,
    ) 