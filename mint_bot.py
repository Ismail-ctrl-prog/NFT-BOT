
import os
import json
import time
from datetime import datetime
import pytz
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import sys

# Constants
CONTRACT_ADDRESS = "0x94F827db182eD0fF90E03713D4ac7AF3184B8f9C"
MINT_FUNCTION_NAME = "mintSeaDrop"
ABI_FILE = "abi.json"
MAX_RETRIES_DURATION = 15 * 60  # 15 minutes in seconds
RETRY_DELAY = 5  # 5 seconds
AST_TIMEZONE = pytz.timezone('Etc/GMT+4') # Atlantic Standard Time (UTC-4)

# Load ABI
try:
    with open(ABI_FILE, 'r') as f:
        CONTRACT_ABI = json.load(f)
except FileNotFoundError:
    print(f"Error: {ABI_FILE} not found.")
    sys.exit(1)

def get_ast_time():
    """Returns the current time in AST."""
    return datetime.now(AST_TIMEZONE)

def setup_web3():
    """Sets up the Web3 connection."""
    rpc_url = os.getenv("RPC_URL")
    if not rpc_url:
        print("Error: RPC_URL environment variable not set.")
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        print("Error: Failed to connect to RPC_URL.")
        sys.exit(1)

    # Add middleware for PoA networks (like Polygon/Sepolia) if needed,
    # but usually safe to add.
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    return w3

def get_account(w3):
    """Loads the account from PRIVATE_KEY."""
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        print("Error: PRIVATE_KEY environment variable not set.")
        sys.exit(1)

    account = w3.eth.account.from_key(private_key)
    return account

def check_balance(w3, account, estimated_gas_cost):
    """Checks if the wallet has enough balance for gas."""
    balance = w3.eth.get_balance(account.address)
    print(f"Current Balance: {w3.from_wei(balance, 'ether')} ETH")
    print(f"Estimated Gas Cost: {w3.from_wei(estimated_gas_cost, 'ether')} ETH")

    if balance < estimated_gas_cost:
        print("Error: Insufficient balance.")
        return False
    return True

def mint_nft(w3, contract, account):
    """Attempts to mint the NFT."""
    try:
        # Build transaction parameters
        # Note: mintSeaDrop(address minter, uint256 quantity)
        # Quantity is assumed 1 based on description.
        # User said "Mint price: 0", so value is 0.

        # Estimate gas
        # We need to build the transaction to estimate gas
        func_call = contract.functions.mintSeaDrop(account.address, 1)

        try:
            gas_estimate = func_call.estimate_gas({'from': account.address, 'value': 0})
        except Exception as e:
            # Check if this is the "not yet active" error
            error_msg = str(e)
            # Some errors might come as "execution reverted: ..."
            print(f"Gas estimation failed: {error_msg}")
            raise e

        # Get gas price
        gas_price = w3.eth.gas_price
        # EIP-1559 support check could be added, but legacy is often safer for simple bots unless configured
        # Using legacy for simplicity or dynamic fee if chain supports it.
        # Web3.py usually handles 'gasPrice' or 'maxFeePerGas' automatically in build_transaction if not specified?
        # Better to be explicit or let web3 handle it.
        # Let's verify balance first.

        total_cost = gas_estimate * gas_price

        if not check_balance(w3, account, total_cost):
            return None, "Insufficient Funds"

        # Build transaction
        chain_id = w3.eth.chain_id
        nonce = w3.eth.get_transaction_count(account.address)

        tx_data = func_call.build_transaction({
            'chainId': chain_id,
            'gas': int(gas_estimate * 1.2), # Add buffer
            'gasPrice': gas_price,
            'nonce': nonce,
            'value': 0
        })

        # Sign transaction
        signed_tx = w3.eth.account.sign_transaction(tx_data, private_key=account.key)

        # Send transaction
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)

        # Wait for receipt (optional, but good for confirmation logging)
        print(f"Transaction sent: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt.status == 1:
            print("Mint successful!")
            return tx_hash.hex(), None
        else:
            return tx_hash.hex(), "Transaction failed (reverted on chain)"

    except Exception as e:
        return None, str(e)

def attempt_mint_window(w3, contract, account):
    """Tries to mint for 15 minutes."""
    start_time = time.time()
    print(f"Starting mint window at {datetime.now()}")

    while time.time() - start_time < MAX_RETRIES_DURATION:
        tx_hash, error = mint_nft(w3, contract, account)

        if tx_hash and not error:
            print(f"Successfully minted! Hash: {tx_hash}")
            return True

        if error:
            print(f"Attempt failed: {error}")

            # Check for "not yet active" type errors to retry
            # Since specific error string isn't guaranteed, we retry on logic errors that seem like timing issues.
            # User said: "if a mint attempt fails because the 'mint is not yet active'"
            # We will retry generally if it seems like a contract rejection that could be time-based.
            # We will assume most contract logic errors are retryable in this window.
            # But checking for specific strings is safer if known.
            # Common strings: "MintNotYetActive", "Public mint not active", "Not active"

            lower_error = error.lower()
            retryable = "not yet active" in lower_error or "not active" in lower_error or "execution reverted" in lower_error

            if retryable:
                print(f"Waiting {RETRY_DELAY} seconds before retrying...")
                time.sleep(RETRY_DELAY)
                continue
            else:
                # If it's something else (e.g. insufficient funds was handled in mint_nft returning None, but here we see 'Insufficient Funds' string from check_balance wrapper if we returned it)
                if "Insufficient Funds" in error:
                    print("Stopping retries due to insufficient funds.")
                    return False

                # Default behavior: retry anyway? The prompt says "Persistence... if mint is not yet active".
                # It doesn't say "Stop if other error".
                # But if we get "Insufficient Funds" we should stop.
                # If we get network error, we retry.
                # I'll retry on everything except fatal config issues, just to be safe and persistent.
                print(f"Retrying in {RETRY_DELAY} seconds...")
                time.sleep(RETRY_DELAY)
                continue

    print("Mint window timed out without success.")
    return False

def main():
    print("Initializing Mint Bot...")
    w3 = setup_web3()
    account = get_account(w3)
    contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=CONTRACT_ABI)

    print(f"Bot running. Wallet: {account.address}")
    print(f"Target Contract: {CONTRACT_ADDRESS}")
    print("Waiting for schedule: 12:15 PM AST and 01:00 PM AST")

    # State to avoid double execution in the same window
    last_run_window = None # (day, window_index)

    # Determine exit time: 13:00 + 15 minutes = 13:15
    start_date = get_ast_time().date()

    while True:
        now_ast = get_ast_time()
        current_day = now_ast.date()

        if current_day > start_date:
             print("New day detected. Stopping bot as requested (run only for today).")
             break

        # Check if we are past the last window retry period (13:15)
        # 13:00 is hour 13, minute 0. + 15 mins = 13:15.
        if now_ast.hour > 13 or (now_ast.hour == 13 and now_ast.minute >= 16):
            print("Today's minting windows (12:15 and 13:00 AST) have passed. Exiting.")
            break

        # Check windows
        # Window 1: 12:15
        if now_ast.hour == 12 and now_ast.minute == 15:
            window_id = (current_day, 1)
            if last_run_window != window_id:
                print(f"Triggering 12:15 PM AST Window at {now_ast}")
                success = attempt_mint_window(w3, contract, account)
                if success:
                    print("12:15 PM window successful. Waiting for next window...")
                last_run_window = window_id

        # Window 2: 13:00 (1:00 PM)
        elif now_ast.hour == 13 and now_ast.minute == 0:
            window_id = (current_day, 2)
            if last_run_window != window_id:
                print(f"Triggering 01:00 PM AST Window at {now_ast}")
                success = attempt_mint_window(w3, contract, account)
                if success:
                    print("13:00 PM window successful. Exiting for the day.")
                    break
                last_run_window = window_id

        # Sleep a bit
        time.sleep(1)

if __name__ == "__main__":
    main()
