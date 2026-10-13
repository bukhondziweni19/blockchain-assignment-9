"""
verify.py
Assignment 9: Financial Blockchain Capstone (the independent verifier)
Blockchain module (BLCH9X2), Master of Financial Engineering, University of Johannesburg

WHAT THIS PROGRAM DOES
    Lets any party check a payment without trusting the platform. It downloads the
    chain from a node, then recomputes everything on this side:

        audit_chain     replays the whole chain: every hash, link, proof of work, reward,
                        signature, sequence number and balance, from genesis
        locate          finds one transaction and reports its block and confirmations
        check_receipt   checks an off-ramp receipt against the chain and the reserve
                        agent's signature

    The only thing taken from the node is the list of blocks. Every verdict printed
    here is worked out locally, so a node that lied about a payment would be caught.

HOW TO RUN (a node must already be running, see payment_api.py or "python demo.py --hold")
    python verify.py                                   audit the chain of the node on port 5000
    python verify.py --txid <transaction id>           also prove one transaction is in the chain
    python verify.py --receipt RCPT-0001               also check one off-ramp receipt
    python verify.py --genesis <hash>                  insist on one particular genesis block
    python verify.py --node http://127.0.0.1:5001      read a different node
"""

import argparse             # argparse reads options such as --txid from the command line

import requests             # requests sends HTTP (HyperText Transfer Protocol) requests to the node

from remittance import receipt_signature_valid                              # the receipt signature check
from simple_blockchain import Block, Transaction, Wallet, tx_id             # blocks, transactions, the signature check and identifiers
from stablecoin import ASSET, replay                                        # the asset name and the full chain validation

# ===========================================================================
# CONFIGURATION
# ===========================================================================

DEFAULT_NODE = "http://127.0.0.1:5000"      # the node that is read unless another is named
REQUEST_TIMEOUT = 120                       # seconds to wait for the node, long enough for a block to be mined

# ===========================================================================
# TALKING TO A NODE
# ===========================================================================

def call_node(method: str, node: str, path: str, payload=None) -> tuple:
    """Send one request to a node. Return (status code, decoded JSON), or (None, None) if it could not be reached."""
    base = node if "//" in node else "http://" + node           # accept "127.0.0.1:5000" as well as a full web address
    try:                                                        # the node may be switched off
        with requests.Session() as session:                     # a session is one short-lived connection manager
            session.trust_env = False                           # ignore any proxy settings: a local node is reached directly
            response = session.request(method, base.rstrip("/") + path, json=payload, timeout=REQUEST_TIMEOUT)   # send the request and wait for the reply
        return response.status_code, response.json()            # hand back the status code and the decoded body
    except (requests.RequestException, ValueError):             # connection problems, timeouts and non-JSON replies land here
        return None, None                                       # tell the caller the node could not be used

# ===========================================================================
# CHECKS WORKED OUT ON THIS SIDE
# ===========================================================================

def audit_chain(chain: list, genesis_hash=None) -> dict:
    """Replay a chain received as a list of dictionaries and report whether every rule holds."""
    blocks = [Block.from_dict(item) for item in chain] if isinstance(chain, list) else []   # rebuild each block, keeping the hash it CLAIMS
    trail = []                                                              # receives one line per block that passes
    state, problem = replay(blocks, genesis_hash, trail)                    # every rule, from genesis, recomputed here
    return {                                                                # the audit report
        "valid": problem is None,                                           # True only if nothing was wrong
        "problem": problem,                                                 # the first broken rule, or None
        "chain_length": len(blocks),                                        # how many blocks were received
        "blocks_passed": len(trail),                                        # how many passed before any problem
        "signatures_checked": sum(line["signatures"] for line in trail),    # how many signatures were verified
        "genesis_hash": blocks[0].hash if blocks and blocks[0] is not None else None,   # which network this chain belongs to
        "reserve_agent": state.issuer if state else None,                   # who may mint, read from block 0
        "minted": state.minted if state else None,                          # mZAR ever created
        "burned": state.burned if state else None,                          # mZAR ever destroyed
        "supply": state.supply if state else None,                          # mZAR in circulation
        "checks": trail,                                                    # the line for each block
    }


def locate(chain: list, txid: str):
    """Find a transaction in a chain. Return where it is and whether its signature holds, or None if it is absent."""
    for item in chain:                                                      # search every block
        for position, tx in enumerate(item["transactions"]):                # and every record inside it
            if tx_id(tx) != txid:                                           # recompute the identifier rather than trusting a label
                continue                                                    # not this one
            signed = tx.get("signature") is not None                        # rewards and the charter carry no signature
            signature_valid = Wallet.verify(Transaction.from_dict(tx).payload_bytes(), tx["signature"], tx["public_key"]) if signed else None   # check it here
            return {                                                        # the proof of inclusion
                "txid": txid,                                               # the identifier that was asked for
                "block_index": item["index"],                               # the block that holds it
                "block_hash": item["hash"],                                 # the hash that block claims
                "block_hash_recomputed": Block.from_dict(item).compute_hash(),   # the hash worked out here from the block's contents
                "position_in_block": position,                              # its place in the block, where 0 is the reward
                "confirmations": chain[-1]["index"] - item["index"] + 1,    # k = H - h + 1
                "signature_valid": signature_valid,                         # True, False, or None for an unsigned record
                "transaction": tx,                                          # the transaction itself
            }
    return None                                                             # the transaction is not in this chain


def check_receipt(receipt: dict, chain: list, reserve_agent: str) -> dict:
    """Check an off-ramp receipt against the chain. Return one True or False per test."""
    found = locate(chain, receipt.get("txid", ""))                          # the burn the receipt points at
    burn = found["transaction"] if found else {}                            # the burn's fields, or nothing if it is absent
    return {                                                                # one line per test
        "signed_by_reserve_agent": receipt_signature_valid(receipt, reserve_agent),   # the signature belongs to the address in block 0
        "burn_is_on_chain": found is not None and burn.get("kind") == "burn",         # the transaction exists and really is a burn
        "block_matches": found is not None and (found["block_index"], found["block_hash"]) == (receipt.get("block_index"), receipt.get("block_hash")),   # same block
        "customer_matches": burn.get("sender") == receipt.get("customer"),  # the receipt names whoever burned
        "amount_matches": burn.get("amount") == receipt.get("burned"),      # the receipt states the amount that was burned
    }

# ===========================================================================
# THE REPORT
# ===========================================================================

def render_audit(audit: dict) -> str:
    """Return the audit report as plain text."""
    lines = ["CHAIN AUDIT (every check recomputed locally from the blocks the node supplied)"]   # the heading
    for line in audit["checks"]:                                            # one line per block that passed
        lines.append(f"  block {line['block']}  {line['hash']}")            # the block and its full hash
        lines.append(f"           {line['transactions']} record(s), {line['signatures']} signature(s) checked: {line['result']}")   # what was checked
    if audit["valid"]:                                                      # nothing was wrong
        lines.append(f"  RESULT: VALID. {audit['chain_length']} blocks, {audit['signatures_checked']} signatures, "   # the summary
                     f"{audit['minted']:,} {ASSET} minted, {audit['burned']:,} burned, {audit['supply']:,} in circulation.")   # with the supply figures
        lines.append(f"  Genesis hash : {audit['genesis_hash']}")           # which network this is
        lines.append(f"  Reserve agent: {audit['reserve_agent']}")          # who may mint
    else:                                                                   # a rule was broken
        lines.append(f"  RESULT: INVALID. {audit['problem']}")           # name the first broken rule and the block it is in
    return "\n".join(lines)                                                 # one string with a line break between lines


def render_inclusion(found, txid: str) -> str:
    """Return the proof of inclusion for one transaction as plain text."""
    if found is None:                                                       # the transaction is not in the chain
        return f"TRANSACTION {txid}\n  NOT FOUND in any block of this chain."   # say so plainly
    tx = found["transaction"]                                               # the transaction's fields
    signature = {True: "valid", False: "INVALID", None: "not applicable (unsigned record)"}[found["signature_valid"]]   # the signature verdict in words
    matches = "matches" if found["block_hash"] == found["block_hash_recomputed"] else "DOES NOT MATCH"   # the block hash verdict in words
    return "\n".join([                                                      # one fact per line
        f"TRANSACTION {txid}",                                              # the identifier
        f"  Contents      : {tx.get('kind', 'reward')} of {tx['amount']:,} from {tx['sender']} to {tx['recipient']}",   # what it does
        f"  Signature     : {signature}",                                   # checked against the public key inside the transaction
        f"  Included in   : block {found['block_index']}, position {found['position_in_block']}",   # where it sits
        f"  Block hash    : {found['block_hash']}",                         # the hash the block claims
        f"  Recomputed    : {found['block_hash_recomputed']} ({matches})",  # the hash worked out here
        f"  Confirmations : {found['confirmations']}",                      # how deep it is
    ])

# ===========================================================================
# RUNNING THE VERIFIER FROM THE COMMAND LINE
# ===========================================================================

def main():
    """Download a node's chain, audit it, and optionally prove one transaction or check one receipt."""
    parser = argparse.ArgumentParser(description="Independently verify a payment against a node's chain.")   # describes the program for --help
    parser.add_argument("--node", default=DEFAULT_NODE, help="address of the node to read (default http://127.0.0.1:5000)")   # which node
    parser.add_argument("--txid", help="a transaction identifier to prove is in the chain")       # optional
    parser.add_argument("--receipt", help="an off-ramp receipt identifier to check, such as RCPT-0001")   # optional
    parser.add_argument("--genesis", help="the genesis hash the chain must start from")           # optional
    arguments = parser.parse_args()                                         # read what was typed
    status, reply = call_node("GET", arguments.node, "/chain")              # the only data taken on trust: the blocks themselves
    if status != 200:                                                       # the node did not answer
        raise SystemExit(f"No node answered at {arguments.node}. Start one with: python payment_api.py")   # explain how to fix it
    chain = reply["chain"]                                                  # the list of blocks
    audit = audit_chain(chain, arguments.genesis)                           # replay everything locally
    print(render_audit(audit))                                              # the audit report
    if not audit["valid"]:                                                  # nothing in an invalid chain can be relied on
        raise SystemExit(1)                                                 # stop with an error status
    if arguments.txid:                                                      # a transaction was named
        print()                                                             # a blank line
        print(render_inclusion(locate(chain, arguments.txid), arguments.txid))   # the proof of inclusion
    if arguments.receipt:                                                   # a receipt was named
        status, receipt = call_node("GET", arguments.node, f"/receipts/{arguments.receipt}")   # fetch the receipt from the node
        if status != 200:                                                   # the node holds no such receipt
            raise SystemExit(f"The node holds no receipt {arguments.receipt}.")   # say so
        print(f"\nRECEIPT {receipt['id']}")                                 # the heading
        for test, passed in check_receipt(receipt, chain, audit["reserve_agent"]).items():   # run each test
            print(f"  {test:<24}: {'yes' if passed else 'NO'}")             # one line per test


if __name__ == "__main__":      # True only when this file is run directly, not when it is imported
    main()                      # run the verifier
