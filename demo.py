"""
demo.py
Assignment 9: Financial Blockchain Capstone (the scripted demonstration)
Blockchain module (BLCH9X2), Master of Financial Engineering, University of Johannesburg

WHAT THIS PROGRAM DOES
    Starts one node on this computer, then drives it through its API (Application
    Programming Interface) exactly as any outside client would, printing every request
    and every reply. The sections follow the demonstration script in the brief:

        Section 1  (i)    create the wallets
        Section 2  (ii)   on-ramp: the Sender deposits 1,000 rand and receives 1,000 mZAR
        Section 3  (iii)  transfer: the Sender signs 750 mZAR to the Recipient, and a block is mined
        Section 4  (iv)   off-ramp: the Recipient redeems 750 mZAR and receives a signed receipt
        Section 5  (v)    verification by an outside party, and the explorer
        Section 6  (vi)   six invalid requests, each refused
        Section 7         the same payment flow again, through one API call
        Section 8         self-checks, ending in a count of checks passed

HOW TO RUN
    python demo.py                    the full demonstration, a few seconds
    python demo.py --hold             keep the node running afterwards, so that
                                      http://127.0.0.1:5000/explorer can be opened
    python demo.py --port 5100        use a particular port (by default 5000, or the next free port)
    python demo.py --difficulty 3     mine with three leading zeros instead of four

    The program also writes everything it prints to sample_outputs.txt.

REPRODUCIBILITY
    The demonstration keys are derived from a fixed seed, signatures are deterministic
    and timestamps come from a logical clock. Every address, signature, transaction
    identifier, nonce and block hash printed is therefore identical on any machine.
    Only the "seconds" value in each mining reply differs.
"""

import argparse          # argparse reads options such as --hold from the command line
import contextlib        # contextlib lets us send print() output through our own object for a while
import copy              # copy.deepcopy makes an independent duplicate of a downloaded chain
import json              # json prints replies in an indented, readable form
import sys               # sys.stdout is the screen that print() normally writes to

from explorer import fetch_snapshot, render_payment_text, render_text       # the explorer's reader and its two text views
from payment_api import first_free_port, start_background_node, stop_background_nodes   # find a port, then start and stop a node inside this program
from simple_blockchain import Transaction, Wallet                           # used by the Sender, who signs on the Sender's own device
from stablecoin import ASSET, DEMO_DIFFICULTY                               # the asset name and the default difficulty
from verify import audit_chain, call_node, check_receipt, locate, render_audit, render_inclusion   # the outside party's checks

CHECKS = []                            # every self-check is recorded here as (description, passed)
OUTPUT_FILE = "sample_outputs.txt"     # the file that receives a copy of everything printed
DEMO_SEED = "blch9x2-capstone-demo"    # the fixed seed the node derives its demonstration keys from
CLIENT_SEED = "blch9x2-capstone-demo-client"   # a separate seed for keys held outside the node, which the node is never given

# ===========================================================================
# PRINTING HELPERS
# ===========================================================================

class Tee:
    """Passes printed text on to the screen while also keeping a copy, like a T-shaped pipe."""

    def __init__(self, screen):
        """Remember the screen and start with an empty copy."""
        self.screen = screen          # where the text should still appear
        self.copy = []                # every piece of text printed so far

    def write(self, text: str) -> int:
        """Called by print() for each piece of text."""
        self.screen.write(text)       # show the text on the screen as usual
        self.copy.append(text)        # and keep a copy for the output file
        return len(text)              # print() expects to be told how many characters were written

    def flush(self):
        """Called by Python when it wants pending text pushed out."""
        self.screen.flush()           # pass the request on to the screen


def section(title: str):
    """Print a heading for one section of the demonstration."""
    print()                       # a blank line before the heading
    print("=" * 110)              # a rule above
    print(title)                  # the heading itself
    print("=" * 110)              # a rule below


def step(title: str):
    """Print a heading for one step within a section."""
    print()                       # a blank line before the heading
    print("-" * 110)              # a rule above
    print(title)                  # the heading itself
    print("-" * 110)              # a rule below


def call(method: str, node: str, path: str, body=None, show: bool = True) -> tuple:
    """Send one request, print it with its reply, and return (status code, reply)."""
    status, reply = call_node(method, node, path, body)           # the request itself
    if show:                                                      # most calls are printed in full
        print(f"{method} {node}{path}")                           # what was asked
        if body is not None:                                      # only POST requests carry a body
            print(f"body: {json.dumps(body)}")                    # the JSON (JavaScript Object Notation) that was sent
        print(f"HTTP {status}")                                   # the status code of the reply
        print(json.dumps(reply, indent=2))                        # the reply, indented for reading
        print()                                                   # a blank line after each exchange
    return status, reply                                          # hand both back so later steps can use them


def check(description: str, passed: bool):
    """Record one self-check for the summary at the end."""
    CHECKS.append((description, bool(passed)))   # store the description with its outcome


def show_balances(node: str, people: dict):
    """Print the confirmed mZAR balance of each named address, and return the balances."""
    balances = {}                                                        # name -> confirmed balance
    print(f"  {'wallet':<16}{'confirmed ' + ASSET:>16}   address")       # column headings
    for name, address in people.items():                                 # one line per wallet
        status, reply = call("GET", node, f"/accounts/{address}", show=False)   # read the account without printing the raw reply
        balances[name] = reply["confirmed"]                              # keep the confirmed figure
        print(f"  {name:<16}{reply['confirmed']:>16,}   {address}")      # name, balance and full address
    print()                                                              # a blank line after the table
    return balances                                                      # hand the figures back for the self-checks

# ===========================================================================
# WHAT A SELF-CUSTODY CUSTOMER DOES ON THEIR OWN DEVICE
# ===========================================================================

def sign_locally(wallet: Wallet, node: str, recipient: str, amount: int, timestamp: int, kind: str = "transfer") -> dict:
    """Build and sign a stablecoin transaction with a key the platform never sees."""
    status, account = call("GET", node, f"/accounts/{wallet.address}", show=False)   # ask the node which sequence number comes next
    tx = Transaction(                               # the unsigned transaction
        sender=wallet.address,                      # the signer's own address
        recipient=recipient,                        # who is being paid
        amount=amount,                              # how much
        timestamp=timestamp,                        # when it was created
        kind=kind,                                  # normally a transfer
        asset=ASSET,                                # always mZAR
        sequence=account["next_sequence"],          # the signer's next sequence number
        public_key=wallet.public_key_hex,           # so anyone can check the signature
    )
    wallet.sign_transaction(tx)                     # the private key signs every field above, on this side
    return tx.to_dict()                             # only the signed transaction is sent to the node

# ===========================================================================
# SECTION 1: WALLETS
# ===========================================================================

def create_wallets(node: str) -> tuple:
    """Register the Sender (who keeps the key) and the Recipient (whose key the platform holds)."""
    section("SECTION 1  CREATE THE WALLETS (brief item a, demonstration step i)")   # print the heading for this section

    step("Step 1  The node at the start: one genesis block, whose charter names the reserve agent")   # print the heading for this step
    status, status_reply = call("GET", node, "/")                                        # the node's status report
    check("GET / returns 200 and a chain holding only the genesis block", status == 200 and status_reply["chain_length"] == 1)   # nothing has been mined yet

    step("Step 2  The Sender creates a key pair on the Sender's own device and registers only its address (self-custody)")   # print the heading for this step
    sender = Wallet.from_seed(f"{CLIENT_SEED}/Sender", "Sender")                           # a repeatable demonstration key, held on this side only
    print(f"Created locally: {sender}  (the private key never leaves this program)\n")   # show the address, never the key
    status, reply = call("POST", node, "/wallets", {"name": "Sender", "address": sender.address})   # tell the platform the address
    check("the Sender is registered with custody 'self'", status == 201 and reply["wallet"]["custody"] == "self")   # the platform holds no key for the Sender

    step("Step 3  The Recipient asks the platform to create and hold a wallet (hosted custody)")   # print the heading for this step
    status, reply = call("POST", node, "/wallets", {"name": "Recipient"})                # no address supplied, so the platform creates the key
    recipient = reply["wallet"]["address"]                                               # the Recipient's address
    check("the Recipient is registered with custody 'hosted'", status == 201 and reply["wallet"]["custody"] == "hosted")   # the platform holds the key

    step("Step 4  Every wallet the platform knows: two operator wallets and two customers")   # print the heading for this step
    status, reply = call("GET", node, "/wallets")                                        # the public records
    check("GET /wallets lists four wallets and reveals no private key", reply["count"] == 4 and "private_key" not in json.dumps(reply))   # public data only
    return sender, recipient, status_reply["reserve_agent"]                              # later sections need all three

# ===========================================================================
# SECTION 2: ON-RAMP
# ===========================================================================

def on_ramp(node: str, sender: Wallet, people: dict) -> str:
    """The Sender deposits 1,000 rand and is issued 1,000 mZAR."""
    section("SECTION 2  ON-RAMP: 1,000 RAND IN, 1,000 mZAR OUT (brief item b, demonstration step ii)")   # print the heading for this section

    step("Step 5  The Sender deposits 1,000 rand. The reserve records it, then the reserve agent signs a mint")   # print the heading for this step
    status, reply = call("POST", node, "/onramp", {"address": sender.address, "amount": 1000, "bank_reference": "EFT-20261013-001"})   # the deposit
    mint_txid = reply["conversion"]["txid"]                                              # the mint's identifier
    check("POST /onramp returns 201 and conversion DEP-0001", status == 201 and reply["conversion"]["id"] == "DEP-0001")   # the conversion event is recorded
    check("the reserve holds 1,000 rand before the mint is mined", reply["reserve_balance"] == 1000)   # money in first, coins second

    step("Step 6  Block 1 is mined and carries the mint")   # print the heading for this step
    status, reply = call("POST", node, "/mine")                                          # the proof-of-work search
    check("block 1 holds the reward and the mint", status == 201 and reply["block"]["index"] == 1 and len(reply["block"]["transactions"]) == 2)   # two records
    check(f"block 1's hash starts with {reply['block']['difficulty']} zeros", reply["block"]["hash"].startswith("0" * reply["block"]["difficulty"]))   # proof of work

    step("Step 7  The conversion record is now confirmed, and the Sender holds 1,000 mZAR")   # print the heading for this step
    status, reply = call("GET", node, "/receipts/DEP-0001")                              # the conversion record
    check("conversion DEP-0001 is confirmed in block 1", reply["status"] == "confirmed" and reply["block_index"] == 1)   # where the mint was mined
    balances = show_balances(node, people)                                               # the balances after the on-ramp
    check("the Sender holds 1,000 mZAR", balances["Sender"] == 1000)                     # one coin per rand
    return mint_txid                                                                     # later sections refer to the mint

# ===========================================================================
# SECTION 3: TRANSFER
# ===========================================================================

def transfer(node: str, sender: Wallet, recipient: str, people: dict) -> str:
    """The Sender signs a transfer of 750 mZAR on the Sender's own device, and a block is mined."""
    section("SECTION 3  TRANSFER: THE SENDER SIGNS 750 mZAR TO THE RECIPIENT (brief item c, demonstration step iii)")   # print the heading for this section

    step("Step 8  The Sender builds and signs the transfer locally, then submits the signed transaction")   # print the heading for this step
    status, chain = call("GET", node, "/chain", show=False)                              # read the chain to take a time just after the newest block
    signed = sign_locally(sender, node, recipient, 750, chain["chain"][-1]["timestamp"] + 30)   # signed with a key the platform has never seen
    status, reply = call("POST", node, "/transactions", signed)                          # only the finished transaction is sent
    transfer_txid = reply["txid"]                                                        # the transfer's identifier
    check("POST /transactions returns 201 and a 64 character txid", status == 201 and len(transfer_txid) == 64)   # accepted into the pool

    step("Step 9  Before mining, the transfer is pending and has no confirmations")   # print the heading for this step
    status, reply = call("GET", node, f"/transactions/{transfer_txid}", show=False)      # look it up without printing the whole reply
    print(f"status: {reply['status']}, confirmations: {reply['confirmations']}\n")       # the two facts that matter here
    check("the transfer is pending with 0 confirmations", reply["status"] == "pending" and reply["confirmations"] == 0)   # not yet in a block

    step("Step 10  Block 2 is mined and carries the signed transfer")   # print the heading for this step
    status, reply = call("POST", node, "/mine")                                          # the proof-of-work search
    check("block 2 points at block 1", reply["block"]["index"] == 2 and reply["block"]["previous_hash"] == chain["chain"][-1]["hash"])   # the link

    step("Step 11  The transfer is confirmed: 250 mZAR stays with the Sender, 750 mZAR is with the Recipient")   # print the heading for this step
    status, reply = call("GET", node, f"/transactions/{transfer_txid}")                  # look it up again
    check("the transfer is confirmed in block 2 with 1 confirmation", reply["status"] == "confirmed" and reply["block_index"] == 2 and reply["confirmations"] == 1)   # mined
    balances = show_balances(node, people)                                               # the balances after the transfer
    check("the Sender holds 250 and the Recipient 750", balances["Sender"] == 250 and balances["Recipient"] == 750)   # value moved, none created
    return transfer_txid                                                                 # later sections refer to the transfer

# ===========================================================================
# SECTION 4: OFF-RAMP
# ===========================================================================

def off_ramp(node: str, recipient: str, people: dict) -> dict:
    """The Recipient redeems 750 mZAR and is paid out 750 loti, with a signed receipt."""
    section("SECTION 4  OFF-RAMP: 750 mZAR IN, 750 LOTI OUT (brief item d, demonstration step iv)")   # print the heading for this section

    step("Step 12  The Recipient redeems 750 mZAR. The platform signs a burn with the Recipient's hosted key")   # print the heading for this step
    status, reply = call("POST", node, "/offramp", {"address": recipient, "amount": 750})   # the redemption request
    burn_txid = reply["txid"]                                                            # the burn's identifier
    check("POST /offramp returns 202: accepted, nothing paid out yet", status == 202 and reply["status"] == "awaiting confirmation")   # no payout before confirmation
    status, reserve = call("GET", node, "/reserve", show=False)                          # read the reserve without printing it
    check("the reserve still holds 1,000 rand while the burn is unconfirmed", reserve["reserve_balance"] == 1000)   # money leaves only after the burn is mined

    step("Step 13  Block 3 is mined. The burn is now confirmed, so the payout is made and a receipt is issued")   # print the heading for this step
    status, reply = call("POST", node, "/mine")                                          # the proof-of-work search
    check("mining block 3 issued receipt RCPT-0001", len(reply["receipts_issued"]) == 1 and reply["receipts_issued"][0]["id"] == "RCPT-0001")   # the payout was triggered

    step("Step 14  The recorded receipt, signed by the reserve agent")   # print the heading for this step
    status, receipt = call("GET", node, "/receipts/RCPT-0001")                           # fetch the receipt
    check("the receipt records 750 mZAR burned and 750 LSL paid", receipt["burned"] == 750 and receipt["paid_out"] == 750 and receipt["status"] == "paid")   # one for one
    check("the receipt names the burn and its block", receipt["txid"] == burn_txid and receipt["block_index"] == 3)   # the link to the chain

    step("Step 15  The reserve after the payout, set against the mZAR in circulation")   # print the heading for this step
    status, reserve = call("GET", node, "/reserve")                                      # the proof-of-reserves report
    check("250 rand in reserve backs 250 mZAR in circulation", reserve["reserve_balance"] == 250 and reserve["supply"] == 250 and reserve["fully_backed"])   # the reserve rule
    balances = show_balances(node, people)                                               # the balances after the off-ramp
    check("the Recipient holds 0 mZAR after redeeming", balances["Recipient"] == 0)      # the coins were destroyed
    print("Where the Sender's 1,000 rand is now: 750 was paid to the Recipient in loti, and 250 remains in")   # a plain-language summary
    print("reserve behind the 250 mZAR the Sender still holds.")                         # nothing was created or lost
    return receipt                                                                       # the verification section checks this receipt

# ===========================================================================
# SECTION 5: VERIFICATION AND EXPLORER
# ===========================================================================

def verification(node: str, transfer_txid: str, receipt: dict):
    """An outside party downloads the chain and checks the payment without trusting the platform."""
    section("SECTION 5  VERIFICATION AND EXPLORER (brief items e and g, demonstration step v)")   # print the heading for this section

    step("Step 16  The node audits its own chain")   # print the heading for this step
    status, reply = call("GET", node, "/chain/verify")                                   # the node's own verdict
    check("GET /chain/verify reports a valid chain of 4 blocks", reply["valid"] and reply["chain_length"] == 4)   # genesis plus three mined blocks

    step("Step 17  An outside party downloads the blocks and recomputes everything without trusting the node")   # print the heading for this step
    status, reply = call("GET", node, "/chain", show=False)                              # the only data taken from the node: the blocks
    chain = reply["chain"]                                                               # the list of blocks
    audit = audit_chain(chain, reply["genesis_hash"])                                    # replayed on this side, from genesis
    print(render_audit(audit))                                                           # the audit report
    check("the outside audit finds the chain valid and checks 3 signatures", audit["valid"] and audit["signatures_checked"] == 3)   # mint, transfer and burn
    check("the outside audit finds 250 mZAR in circulation", audit["supply"] == 250)     # 1,000 minted less 750 burned

    step("Step 18  Transaction lookup: the transfer is proved to be inside block 2")   # print the heading for this step
    found = locate(chain, transfer_txid)                                                 # search the downloaded blocks
    print(render_inclusion(found, transfer_txid))                                        # the proof of inclusion
    check("the transfer is found in block 2 with a valid signature", found["block_index"] == 2 and found["signature_valid"])   # included and authentic
    check("block 2's hash matches a hash recomputed from its contents", found["block_hash"] == found["block_hash_recomputed"])   # the block was not edited
    check("the transfer now has 2 confirmations", found["confirmations"] == 2)           # block 3 was built on top of it

    step("Step 19  The receipt is checked against the chain and the reserve agent's signature")   # print the heading for this step
    results = check_receipt(receipt, chain, audit["reserve_agent"])                      # five independent tests
    for test, passed in results.items():                                                 # one line per test
        print(f"  {test:<24}: {'yes' if passed else 'NO'}")                              # the outcome of each
    check("the receipt passes all five tests", all(results.values()))                    # it is genuine and matches the chain

    step("Step 20  Tamper evidence: a copy of the chain is edited and audited again")   # print the heading for this step
    forged = copy.deepcopy(chain)                                                        # a private copy, so the node itself is untouched
    forged[2]["transactions"][1]["amount"] = 7500                                        # change the confirmed 750 into 7,500
    print("In a copy of the chain, the transfer in block 2 is changed from 750 to 7,500 mZAR.\n")   # say what was done
    forged_audit = audit_chain(forged, reply["genesis_hash"])                            # audit the edited copy
    print(render_audit(forged_audit))                                                    # the audit report
    check("the edited copy is rejected at block 2", not forged_audit["valid"] and "Block 2" in forged_audit["problem"])   # the edit is detected

    step("Step 21  The explorer: a human-readable overview of the node")   # print the heading for this step
    snapshot = fetch_snapshot(node)                                                      # read the node through its public endpoints
    print(render_text(snapshot))                                                         # the overview

    step("Step 22  The explorer: the payment record for the transfer, with its block and transaction identifiers")   # print the heading for this step
    print(render_payment_text(snapshot, transfer_txid))                                  # one transaction and its block
    check("the explorer's payment record shows the full transaction identifier", transfer_txid in render_payment_text(snapshot, transfer_txid))   # all 64 characters

# ===========================================================================
# SECTION 6: INVALID REQUESTS
# ===========================================================================

def refusals(node: str, sender: Wallet, recipient: str, reserve_agent: str, transfer_txid: str):
    """Six invalid requests. Each is refused, and none of them changes the ledger."""
    section("SECTION 6  INVALID REQUESTS ARE REFUSED (demonstration step vi)")   # print the heading for this section
    mallory = Wallet.from_seed(f"{CLIENT_SEED}/Mallory", "Mallory")                        # an attacker with her own key and no registration
    print(f"The attacker's wallet: {mallory}")                                           # show the attacker's address
    status, chain = call("GET", node, "/chain", show=False)                              # the chain is public, so the attacker can read it
    now = chain["chain"][-1]["timestamp"] + 30                                           # a time just after the newest block
    confirmed = locate(chain["chain"], transfer_txid)["transaction"]                     # the Sender's confirmed transfer, copied from the chain

    step("Step 23 (a)  Bad signature: the Sender's transfer is copied from the chain and redirected to the attacker")   # print the heading for this step
    redirected = dict(confirmed, recipient=mallory.address)                              # same signature, different recipient
    status, reply = call("POST", node, "/transactions", redirected)                      # submit the altered transaction
    check("an altered transaction is refused with 401", status == 401 and "signature" in reply["error"])   # the signature no longer matches

    step("Step 24 (b)  Wrong key: the attacker signs a transfer out of the Sender's account with her own key")   # print the heading for this step
    forged = sign_locally(mallory, node, mallory.address, 250, now)                      # a transaction the attacker can sign validly
    forged["sender"], forged["sequence"] = sender.address, 1                             # then relabelled as coming from the Sender
    status, reply = call("POST", node, "/transactions", forged)                          # submit it
    check("a transaction signed with the wrong key is refused with 401", status == 401 and "public key" in reply["error"])   # the key is not the Sender's

    step("Step 25 (c)  Insufficient balance: the Sender holds 250 mZAR and validly signs a transfer of 500")   # print the heading for this step
    status, reply = call("POST", node, "/transactions", sign_locally(sender, node, recipient, 500, now))   # a genuine signature, but too large
    check("an unaffordable transfer is refused with 422", status == 422 and "250" in reply["error"])   # the ledger says what is held

    step("Step 26 (d)  Replay: the Sender's confirmed transfer is submitted a second time, unchanged")   # print the heading for this step
    status, reply = call("POST", node, "/transactions", confirmed)                       # the signature is genuine, but it was used already
    check("a replayed transaction is refused with 409", status == 409)                   # it cannot move value twice

    step("Step 27 (e)  Forged mint: the attacker signs a mint of 1,000,000 mZAR to herself")   # print the heading for this step
    status, reply = call("POST", node, "/transactions", sign_locally(mallory, node, mallory.address, 1_000_000, now, kind="mint"))   # validly signed, but not by the reserve agent
    check("a mint by anyone but the reserve agent is refused with 403", status == 403 and "reserve agent" in reply["error"])   # only the charter's address may mint

    step("Step 28 (f)  Unregistered customer: the attacker asks the on-ramp to mint to an unregistered address")   # print the heading for this step
    status, reply = call("POST", node, "/onramp", {"address": mallory.address, "amount": 1000})   # no registration, so no deposit is taken
    check("the on-ramp refuses an unregistered address with 403", status == 403)          # the know-your-customer gate

    step("Step 29  Nothing changed: the pool is empty, the chain is the same length and the reserve still matches")   # print the heading for this step
    status, pool = call("GET", node, "/transactions/pending")                            # the pending pool
    status, after = call("GET", node, "/chain", show=False)                              # the chain after the six attempts
    status, reserve = call("GET", node, "/reserve", show=False)                          # the reserve after the six attempts
    print(f"chain length {after['length']}, newest hash {after['chain'][-1]['hash']}")   # the same tip as before
    print(f"reserve {reserve['reserve_balance']:,} {reserve['reserve_currency']}, supply {reserve['supply']:,} {ASSET}\n")   # the same figures as before
    check("no refused request entered the pending pool", pool["count"] == 0)             # the pool is still empty
    check("the chain is unchanged by the refused requests", after["chain"][-1]["hash"] == chain["chain"][-1]["hash"])   # the tip did not move
    check("the reserve is unchanged by the refused requests", reserve["reserve_balance"] == 250 and reserve["supply"] == 250)   # no deposit was taken

# ===========================================================================
# SECTION 7: THE WHOLE FLOW IN ONE CALL
# ===========================================================================

def one_call(node: str, recipient: str):
    """A second customer, with a hosted wallet, sends a payment through the single orchestrating endpoint."""
    section("SECTION 7  THE PAYMENT API: THE WHOLE FLOW IN ONE CALL (brief item f)")   # print the heading for this section

    step("Step 30  A second customer registers a hosted wallet")   # print the heading for this step
    status, reply = call("POST", node, "/wallets", {"name": "Second sender"})            # the platform creates and holds the key
    second = reply["wallet"]["address"]                                                  # the new customer's address

    step("Step 31  POST /payments runs on-ramp, transfer and off-ramp for 400, mining a block after each step")   # print the heading for this step
    status, reply = call("POST", node, "/payments", {"sender": second, "recipient": recipient, "amount": 400, "bank_reference": "EFT-20261013-002"})   # one call
    payment = reply["payment"]                                                           # the payment record
    check("POST /payments returns 201 with three confirmed steps", status == 201 and [item["block_index"] for item in payment["steps"]] == [4, 5, 6])   # blocks 4 to 6
    check("the payment produced receipt RCPT-0002", payment["receipt_id"] == "RCPT-0002")   # the second redemption

    step("Step 32  The final state: the chain is valid and the reserve still equals the supply")   # print the heading for this step
    status, audit = call("GET", node, "/chain/verify", show=False)                       # the node's audit
    status, reserve = call("GET", node, "/reserve", show=False)                          # the proof-of-reserves report
    print(f"chain valid: {audit['valid']}, blocks: {audit['chain_length']}, signatures checked: {audit['signatures_checked']}")   # the audit in one line
    print(f"minted {reserve['minted']:,}, burned {reserve['burned']:,}, in circulation {reserve['supply']:,} {ASSET}")   # the supply in one line
    print(f"reserve {reserve['reserve_balance']:,} {reserve['reserve_currency']}, fully backed: {reserve['fully_backed']}")   # the reserve in one line
    check("the final chain of 7 blocks is valid", audit["valid"] and audit["chain_length"] == 7)   # genesis plus six mined blocks
    check("1,400 minted, 1,150 burned, 250 in circulation, 250 in reserve", (reserve["minted"], reserve["burned"], reserve["supply"], reserve["reserve_balance"]) == (1400, 1150, 250, 250))   # the books balance

# ===========================================================================
# RUNNING THE DEMONSTRATION
# ===========================================================================

def main():
    """Start a node, run every section, save the output and report the self-checks."""
    parser = argparse.ArgumentParser(description="Demonstrate a full remittance on one local node.")   # describes the program for --help
    parser.add_argument("--port", type=int, help="port for the node (default: 5000, or the next free port after it)")   # where the node listens
    parser.add_argument("--difficulty", type=int, default=DEMO_DIFFICULTY, help=f"leading zeros required of each block hash (default {DEMO_DIFFICULTY})")   # the mining rule
    parser.add_argument("--hold", action="store_true", help="keep the node running until Enter is pressed")   # for viewing /explorer in a browser
    arguments = parser.parse_args()                                      # read what was typed
    port = arguments.port if arguments.port is not None else first_free_port()   # the named port, or the first free one from 5000
    node = f"http://127.0.0.1:{port}"                                    # the address of the node
    tee = Tee(sys.stdout)                                                # everything printed goes to the screen and into a copy
    try:                                                                 # make sure the node is stopped even if a step fails
        start_background_node(port, seed=DEMO_SEED, difficulty=arguments.difficulty)   # a fresh node with repeatable keys
        with contextlib.redirect_stdout(tee):                            # from here on, print() writes through the tee
            print("BLCH9X2 Assignment 9: Financial Blockchain Capstone, African Remittances Payment Prototype")   # a title for the output
            print(f"One node started at {node}, mining at difficulty {arguments.difficulty}")   # where it is and how hard it mines
            sender, recipient, reserve_agent = create_wallets(node)      # section 1
            people = {"Sender": sender.address, "Recipient": recipient, "Reserve agent": reserve_agent}   # the wallets whose balances are shown
            on_ramp(node, sender, people)                                # section 2
            transfer_txid = transfer(node, sender, recipient, people)    # section 3
            receipt = off_ramp(node, recipient, people)                  # section 4
            verification(node, transfer_txid, receipt)                   # section 5
            refusals(node, sender, recipient, reserve_agent, transfer_txid)   # section 6
            one_call(node, recipient)                                    # section 7
            section("SECTION 8  SELF-CHECKS")                            # print the heading for this section
            print()                                                      # a blank line before the list
            for description, passed in CHECKS:                           # list every check
                print(f"  [{'PASS' if passed else 'FAIL'}] {description}")   # with its outcome
            passed_count = sum(1 for _, passed in CHECKS if passed)      # how many passed
            print(f"\n{passed_count} passed, {len(CHECKS) - passed_count} failed")   # the final count
        with open(OUTPUT_FILE, "w", encoding="utf-8") as output_file:    # open the output file for writing
            output_file.write("".join(tee.copy))                         # write the copy of everything printed
        print(f"\nThis output was also written to {OUTPUT_FILE}")        # say where the copy went
        if arguments.hold:                                               # keep serving if asked to
            print(f"\nThe node is still running. Open {node}/explorer in a browser.")   # where to look
            input("Press Enter to stop it. ")                            # wait for the Enter key
    finally:                                                             # this runs whether or not an error occurred
        stop_background_nodes()                                          # stop the node and free the port


if __name__ == "__main__":      # True only when this file is run directly, not when it is imported
    main()                      # run the demonstration
