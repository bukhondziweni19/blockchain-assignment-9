"""
remittance.py
Assignment 9: Financial Blockchain Capstone (the payment platform)
Blockchain module (BLCH9X2), Master of Financial Engineering, University of Johannesburg

WHAT THIS FILE DOES
    Holds everything that happens off the chain, and connects it to the ledger in
    stablecoin.py. The corridor modelled is South Africa to Lesotho: a sender pays in
    rand (ZAR), and the recipient is paid out in loti (LSL), which is pegged to the
    rand at one for one, so no exchange rate is needed.

        LogicalClock        a repeatable clock, so every run prints the same identifiers
        ReserveBook         the reserve agent's cash book for the money backing mZAR
        RemittancePlatform  wallets and custody, the on-ramp, the off-ramp and receipts

THE THREE LAYERS
    Ledger        the Ledger object: blocks, signatures, balances (stablecoin.py)
    Custody       who holds each private key. A "hosted" wallet's key is held by the
                  platform, which signs on the customer's instruction. A "self" wallet's
                  key never reaches the platform: its owner signs and submits a finished
                  transaction.
    Verification  everything the platform claims can be checked against the chain by
                  an outsider (verify.py), and each receipt is signed by the reserve agent.

THE RESERVE RULE
    Rand is recorded in the reserve BEFORE mZAR is minted, and mZAR is burned and
    confirmed BEFORE loti is paid out. The reserve therefore never falls below the
    mZAR in circulation.

HOW TO RUN
    This file is a library and is not run directly. Run the demonstration with:
        python demo.py
"""

import json                 # json writes a receipt in one agreed text form before it is signed
import threading            # threading provides a lock, so two requests cannot change the platform at the same moment
import time                 # time supplies real timestamps when the wall clock is chosen

from simple_blockchain import Transaction, Wallet, pubkey_to_address, tx_id   # transactions, key pairs, address derivation and transaction identifiers
from stablecoin import ASSET, DEMO_DIFFICULTY, GENESIS_TIMESTAMP, MAX_AMOUNT, NATIVE_ASSET, RESERVE_RULE, Ledger, is_address, is_whole_number   # the stablecoin layer

# ===========================================================================
# CONFIGURATION
# ===========================================================================

DEPOSIT_CURRENCY = "ZAR"            # what the sender pays in: South African rand
PAYOUT_CURRENCY = "LSL"             # what the recipient is paid in: Lesotho loti, pegged to the rand at one for one
CONFIRMATIONS_REQUIRED = 1          # how many blocks must confirm a mint or burn before the platform treats it as final
CLOCK_STEP_SECONDS = 60             # the logical clock moves forward one minute each time it is read
MAX_NAME_LENGTH = 32                # the longest wallet name the platform will accept

# ===========================================================================
# CLOCKS
# ===========================================================================

class LogicalClock:
    """A clock that starts at the genesis time and moves forward a fixed step each time it is read.

    Timestamps then depend only on the order of events, so every run prints the same identifiers."""

    def __init__(self, start: int = GENESIS_TIMESTAMP, step: int = CLOCK_STEP_SECONDS):
        """Set the starting time and the size of each step."""
        self.current = start        # the last time handed out
        self.step = step            # how far the clock moves on each reading

    def now(self) -> int:
        """Move the clock forward one step and return the new time."""
        self.current += self.step   # advance by one step
        return self.current         # the new time, as a Unix timestamp


class WallClock:
    """The real time of day. Identifiers then differ from run to run."""

    def now(self) -> int:
        """Return the current time as a Unix timestamp in whole seconds."""
        return int(time.time())     # the number of seconds since 1 January 1970

# ===========================================================================
# THE RESERVE
# ===========================================================================

class ReserveBook:
    """The reserve agent's cash book: every rand received and every loti paid out, with a running balance.

    It stands in for the bank account that backs the stablecoin. It lives off the chain."""

    def __init__(self):
        """Start with an empty book."""
        self.entries = []           # every deposit and payout, oldest first
        self.balance = 0            # the money currently held in reserve, in rand

    def record(self, kind: str, amount: int, customer: str, reference: str, txid) -> dict:
        """Add one entry. Deposits carry a positive amount and payouts a negative one."""
        self.balance += amount                          # update the running balance
        entry = {                                       # the line written in the book
            "entry": len(self.entries) + 1,             # a running number
            "type": kind,                               # "deposit" or "payout"
            "amount": amount,                           # positive in, negative out
            "currency": DEPOSIT_CURRENCY,               # the reserve is held in rand
            "customer": customer,                       # the customer's address
            "reference": reference,                     # the conversion or receipt this entry belongs to
            "txid": txid,                               # the on-chain transaction it matches
            "balance": self.balance,                    # the balance after this entry
        }
        self.entries.append(entry)                      # keep the entry
        return entry                                    # hand it back to the caller

# ===========================================================================
# RECEIPTS
# ===========================================================================

def receipt_bytes(receipt: dict) -> bytes:
    """Return the exact bytes the reserve agent signs for a receipt: every field except the signature."""
    unsigned = {name: value for name, value in receipt.items() if name != "signature"}   # copy the fields, leaving the signature out
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")   # the same agreed text form used for transactions


def receipt_signature_valid(receipt: dict, issuer_address: str) -> bool:
    """Return True if the receipt was signed by the reserve agent named in the genesis block."""
    try:                                                                    # a receipt from outside may be missing fields
        public_key = receipt["issuer_public_key"]                           # the key the receipt claims to be signed with
        if pubkey_to_address(bytes.fromhex(public_key)) != issuer_address:  # that key must belong to the reserve agent's address
            return False                                                    # otherwise anybody could have signed it
        return Wallet.verify(receipt_bytes(receipt), receipt["signature"], public_key)   # the signature must cover these exact fields
    except (KeyError, TypeError, ValueError):                               # a missing or malformed field lands here
        return False                                                        # an unreadable receipt is not accepted

# ===========================================================================
# THE PLATFORM
# ===========================================================================

class PlatformError(Exception):
    """Raised when a request cannot be carried out. It carries an HTTP (HyperText Transfer Protocol) status code."""

    def __init__(self, status: int, message: str):
        """Remember the status code and the message."""
        super().__init__(message)       # let Python store the message in the usual way
        self.status = status            # the status code the API should answer with
        self.message = message          # the sentence explaining what went wrong


STATUS_FOR_CODE = {                     # how each ledger outcome is reported over HTTP
    "malformed": 400,                   # 400 means "bad request": the fields were wrong
    "bad_signature": 401,               # 401 means "unauthorised": the request did not prove who sent it
    "unauthorised": 403,                # 403 means "forbidden": the sender is known but may not do this
    "replay": 409,                      # 409 means "conflict": the transaction clashes with what the ledger already holds
    "insufficient_funds": 422,          # 422 means "understood, but cannot be processed"
}


class RemittancePlatform:
    """The payment platform: a ledger, a key store, a reserve book and the two conversion desks."""

    def __init__(self, difficulty: int = DEMO_DIFFICULTY, seed=None, clock=None, confirmations_required: int = CONFIRMATIONS_REQUIRED):
        """Create the operator's own wallets, then a ledger whose genesis block names the reserve agent."""
        self.lock = threading.RLock()                               # a re-entrant lock: one request at a time, and a method may call another
        self.seed = seed                                            # None for random keys, or text for repeatable demonstration keys
        self.clock = clock if clock is not None else LogicalClock() # the repeatable clock unless another was supplied
        self.confirmations_required = confirmations_required        # how deep a mint or burn must be before it is treated as final
        self.keys = {}                                              # address -> Wallet, for every key the platform holds
        self.wallets = {}                                           # address -> the public record of each known wallet
        self.reserve_agent = self._create_key("Reserve agent", "operator")   # issues and redeems mZAR, and signs receipts
        self.validator = self._create_key("Validator", "operator")  # mines blocks and receives the native-coin reward
        self.ledger = Ledger(self.reserve_agent.address, difficulty)   # the chain, with the reserve agent written into block 0
        self.reserve = ReserveBook()                                # the cash book for the money backing mZAR
        self.conversions = []                                       # one record per on-ramp deposit
        self.receipts = []                                          # one receipt per off-ramp redemption
        self.payments = []                                          # one record per payment run through pay()

    # -----------------------------------------------------------------------
    # Wallets and custody
    # -----------------------------------------------------------------------
    def _create_key(self, name: str, custody: str) -> Wallet:
        """Create a key pair that the platform itself holds, and record the wallet."""
        if self.seed is None:                                           # normal operation
            wallet = Wallet.create(name)                                # a fresh random key
        else:                                                           # demonstration mode
            wallet = Wallet.from_seed(f"{self.seed}/{name}", name)      # a key derived from the seed and the name, identical on every run
        self.keys[wallet.address] = wallet                              # the platform keeps the private key
        self.wallets[wallet.address] = {                                # the public record, which never includes the private key
            "name": name,                                               # the label shown in the explorer
            "address": wallet.address,                                  # where the wallet is paid
            "custody": custody,                                         # "operator" for the platform's own wallets, "hosted" for customers
            "public_key": wallet.public_key_hex,                        # safe to publish
        }
        return wallet                                                   # hand the wallet back to the caller

    def register_wallet(self, name, address=None) -> dict:
        """Register a customer. With no address the platform creates and holds the key; with an address the customer keeps it."""
        with self.lock:                                                                     # one request at a time
            if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LENGTH or not name.isprintable():   # a short printable name is required
                raise PlatformError(400, f"'name' must be 1 to {MAX_NAME_LENGTH} printable characters")   # explain the rule that failed
            name = name.strip()                                                             # remove any spaces around the name
            if any(record["name"] == name for record in self.wallets.values()):             # names identify customers, so each is used once
                raise PlatformError(409, f"a wallet named '{name}' is already registered")  # refuse the repeat
            if address is None:                                                             # no address supplied: a hosted wallet
                wallet = self._create_key(name, "hosted")                                   # the platform creates and keeps the key
                return dict(self.wallets[wallet.address])                                   # return a copy of the public record
            if not is_address(address):                                                     # an address supplied: it must have the right form
                raise PlatformError(400, "'address' must be a 40 character hexadecimal address")   # explain the rule that failed
            if address in self.wallets:                                                     # each address is registered once
                raise PlatformError(409, "that address is already registered")              # refuse the repeat
            self.wallets[address] = {"name": name, "address": address, "custody": "self", "public_key": None}   # the customer keeps the key
            return dict(self.wallets[address])                                              # return a copy of the public record

    def _customer(self, address, must_be_hosted: bool = False) -> dict:
        """Return the record of a registered customer, or raise an error explaining why the request is refused."""
        record = self.wallets.get(address) if isinstance(address, str) else None            # look the address up
        if record is None or record["custody"] == "operator":                               # unknown, or one of the platform's own wallets
            raise PlatformError(403, "that address is not a registered customer; register the wallet first")   # the know-your-customer gate
        if must_be_hosted and record["custody"] != "hosted":                                # the platform can only sign for keys it holds
            raise PlatformError(403, "the platform does not hold that wallet's key; sign the transaction yourself and send it to POST /transactions")   # point to the other route
        return record                                                                       # the customer's public record

    @staticmethod
    def _amount(value) -> int:
        """Return the amount if it is a whole number in the permitted range, or raise an error."""
        if not is_whole_number(value) or not 1 <= value <= MAX_AMOUNT:                      # decimals, text, booleans, negatives and absurd values are refused
            raise PlatformError(400, f"'amount' must be a whole number from 1 to {MAX_AMOUNT:,}")   # explain the rule that failed
        return value                                                                        # the amount is usable

    def _sign_and_submit(self, signer: Wallet, recipient: str, amount: int, kind: str, reference=None) -> dict:
        """Build a stablecoin transaction, sign it with a key the platform holds, and submit it to the ledger."""
        tx = Transaction(                                                       # the unsigned transaction
            sender=signer.address,                                              # the account being debited (or the reserve agent, for a mint)
            recipient=recipient,                                                # the account being credited
            amount=amount,                                                      # how much
            timestamp=self.clock.now(),                                         # when, on the platform's clock
            kind=kind,                                                          # "mint", "transfer" or "burn"
            asset=ASSET,                                                        # always mZAR
            sequence=self.ledger.account(signer.address)["next_sequence"],      # the signer's next sequence number
            public_key=signer.public_key_hex,                                   # so anyone can check the signature
            reference=reference,                                                # the link to an off-chain record, if any
        )
        signer.sign_transaction(tx)                                             # the private key signs every field above
        code, message, txid = self.ledger.submit(tx.to_dict())                  # the ledger applies every rule before accepting
        if code != "accepted":                                                  # the ledger refused it
            raise PlatformError(STATUS_FOR_CODE[code], message)                 # pass the ledger's reason on
        return {"txid": txid, "transaction": tx.to_dict()}                      # the identifier and the signed transaction

    # -----------------------------------------------------------------------
    # The on-ramp: rand in, mZAR out
    # -----------------------------------------------------------------------
    def on_ramp(self, address, amount, bank_reference=None) -> dict:
        """Record a rand deposit in the reserve, then mint the same number of mZAR to the customer."""
        with self.lock:                                                                 # one request at a time
            customer = self._customer(address)                                          # only registered customers may deposit
            amount = self._amount(amount)                                               # a whole number greater than zero
            if bank_reference is not None and (not isinstance(bank_reference, str) or len(bank_reference) > 64):   # the bank's own reference is optional
                raise PlatformError(400, "'bank_reference' must be text of at most 64 characters")   # explain the rule that failed
            conversion_id = f"DEP-{len(self.conversions) + 1:04d}"                      # a running identifier such as DEP-0001
            entry = self.reserve.record("deposit", amount, address, conversion_id, None)   # step 1: the rand is in the reserve
            minted = self._sign_and_submit(self.reserve_agent, address, amount, "mint", conversion_id)   # step 2: the reserve agent mints against it
            entry["txid"] = minted["txid"]                                              # link the cash-book entry to the mint
            conversion = {                                                              # the record of this conversion event
                "id": conversion_id,                                                    # its identifier, also carried on the chain as the mint's reference
                "type": "on-ramp",                                                      # rand in, mZAR out
                "customer": address,                                                    # who deposited
                "customer_name": customer["name"],                                      # the customer's registered name
                "deposited": amount,                                                    # rand received
                "deposit_currency": DEPOSIT_CURRENCY,                                   # ZAR
                "minted": amount,                                                       # mZAR created: the same number, by the reserve rule
                "asset": ASSET,                                                         # mZAR
                "rate": "1 ZAR = 1 mZAR",                                               # the conversion rate, in words
                "bank_reference": bank_reference,                                       # the bank's reference for the deposit, if given
                "txid": minted["txid"],                                                 # the mint transaction
                "status": "pending",                                                    # it becomes "confirmed" once the mint is mined
                "block_index": None,                                                    # filled in when the mint is mined
                "block_hash": None,                                                     # filled in when the mint is mined
            }
            self.conversions.append(conversion)                                         # keep the record
            return dict(conversion)                                                     # return a copy

    # -----------------------------------------------------------------------
    # Transfers
    # -----------------------------------------------------------------------
    def transfer(self, sender, recipient, amount, reference=None) -> dict:
        """Send mZAR from a hosted wallet. The platform signs on the customer's instruction."""
        with self.lock:                                                                 # one request at a time
            self._customer(sender, must_be_hosted=True)                                 # the platform must hold the sender's key
            amount = self._amount(amount)                                               # a whole number greater than zero
            return self._sign_and_submit(self.keys[sender], recipient, amount, "transfer", reference)   # sign with the hosted key and submit

    def submit_signed(self, data) -> dict:
        """Accept a transaction that was signed outside the platform, by a customer who holds their own key."""
        with self.lock:                                                                 # one request at a time
            code, message, txid = self.ledger.submit(data)                              # the ledger applies every rule
            if code != "accepted":                                                      # the ledger refused it
                raise PlatformError(STATUS_FOR_CODE[code], message)                     # pass the ledger's reason on
            return {"txid": txid, "transaction": data}                                  # the identifier and the transaction as stored

    # -----------------------------------------------------------------------
    # The off-ramp: mZAR in, loti out
    # -----------------------------------------------------------------------
    def off_ramp(self, address, amount) -> dict:
        """Burn mZAR from a hosted wallet. The payout and its receipt follow once the burn is confirmed."""
        with self.lock:                                                                 # one request at a time
            self._customer(address, must_be_hosted=True)                                # the platform must hold the customer's key
            amount = self._amount(amount)                                               # a whole number greater than zero
            burned = self._sign_and_submit(self.keys[address], self.reserve_agent.address, amount, "burn", "redemption")   # the customer's key signs the burn
            burned["status"] = "awaiting confirmation"                                  # nothing is paid out until the burn is in a block
            burned["note"] = f"a receipt is issued and {PAYOUT_CURRENCY} paid out once the burn has {self.confirmations_required} confirmation(s)"   # what happens next
            return burned                                                               # the burn's identifier and status

    def _settle(self, block) -> list:
        """Act on a block that has just become final: confirm its mints and pay out its burns. Return the receipts issued."""
        issued = []                                                                     # receipts created by this block
        for tx in block.transactions[1:]:                                               # every record after the miner's reward
            txid = tx_id(tx)                                                            # the transaction's identifier
            if tx["kind"] == "mint":                                                    # a mint completes an on-ramp conversion
                for conversion in self.conversions:                                     # find the conversion it belongs to
                    if conversion["txid"] == txid:                                      # matched by transaction identifier
                        conversion.update(status="confirmed", block_index=block.index, block_hash=block.hash)   # record where it was mined
            if tx["kind"] == "burn":                                                    # a burn is a redemption that must now be honoured
                issued.append(self._issue_receipt(tx, txid, block))                     # pay out and write the receipt
        return issued                                                                   # the receipts created

    def _issue_receipt(self, tx: dict, txid: str, block) -> dict:
        """Pay out a confirmed burn from the reserve and return a receipt signed by the reserve agent."""
        customer = self.wallets.get(tx["sender"])                                       # the registered record of whoever burned
        known = customer is not None and customer["custody"] != "operator"              # payouts go to registered customers only
        receipt = {                                                                     # the receipt, before signing
            "id": f"RCPT-{len(self.receipts) + 1:04d}",                                 # a running identifier such as RCPT-0001
            "type": "off-ramp",                                                         # mZAR in, loti out
            "customer": tx["sender"],                                                   # who redeemed
            "customer_name": customer["name"] if known else None,                       # the customer's registered name, if any
            "burned": tx["amount"],                                                     # mZAR destroyed
            "asset": ASSET,                                                             # mZAR
            "paid_out": tx["amount"] if known else 0,                                   # loti paid: the same number, because the loti is pegged at par
            "payout_currency": PAYOUT_CURRENCY,                                         # LSL
            "rate": f"1 {ASSET} = 1 {DEPOSIT_CURRENCY} = 1 {PAYOUT_CURRENCY}",          # the conversion rate, in words
            "status": "paid" if known else "held: the burning address is not a registered customer",   # an unknown redeemer is held for review
            "txid": txid,                                                               # the burn transaction
            "block_index": block.index,                                                 # the block that holds the burn
            "block_hash": block.hash,                                                   # that block's fingerprint
            "issued_at": self.clock.now(),                                              # when the receipt was written
            "issuer": self.reserve_agent.address,                                       # the reserve agent's address, as named in the genesis block
            "issuer_public_key": self.reserve_agent.public_key_hex,                     # so anyone can check the signature below
        }
        if known:                                                                       # only a paid receipt moves money
            self.reserve.record("payout", -tx["amount"], tx["sender"], receipt["id"], txid)   # the loti leaves the reserve
        receipt["signature"] = self.reserve_agent.sign(receipt_bytes(receipt))          # the reserve agent signs every field above
        self.receipts.append(receipt)                                                   # keep the receipt
        return dict(receipt)                                                            # return a copy

    # -----------------------------------------------------------------------
    # Mining
    # -----------------------------------------------------------------------
    def mine(self) -> dict:
        """Mine the pending transactions into a block, then settle whichever block has just become final."""
        with self.lock:                                                                 # hold the platform still while the block is mined
            block, attempts, seconds = self.ledger.mine(self.validator.address, self.clock.now())   # the proof-of-work search
            final_index = block.index - self.confirmations_required + 1                 # the block that this new block makes final
            receipts = self._settle(self.ledger.chain[final_index]) if final_index >= 1 else []   # genesis holds nothing to settle
            return {"block": block.to_dict(), "attempts": attempts, "seconds": seconds, "receipts_issued": receipts}   # what happened

    # -----------------------------------------------------------------------
    # One call for the whole payment
    # -----------------------------------------------------------------------
    def pay(self, sender, recipient, amount, redeem: bool = True, bank_reference=None) -> dict:
        """Run a whole remittance between two hosted wallets: on-ramp, transfer and (optionally) off-ramp, mining after each step."""
        with self.lock:                                                                 # one payment at a time, start to finish
            self._customer(sender, must_be_hosted=True)                                 # check everything first, so a payment never stops half way
            self._customer(recipient, must_be_hosted=redeem)                            # the recipient's key is needed only if the platform redeems for them
            amount = self._amount(amount)                                               # a whole number greater than zero
            if sender == recipient:                                                     # paying yourself moves nothing
                raise PlatformError(400, "'sender' and 'recipient' must be different")  # so it is refused
            steps = []                                                                  # the trail of on-chain evidence, one line per step

            def confirm(name: str, txid: str) -> list:
                """Mine until the step is final, then add it to the trail. Return any receipts issued."""
                issued = []                                                             # receipts created while confirming this step
                for _ in range(self.confirmations_required):                            # mine as many blocks as finality needs
                    issued += self.mine()["receipts_issued"]                            # each block may settle earlier transactions
                found = self.ledger.find_transaction(txid)                              # where the transaction ended up
                steps.append({"step": name, "txid": txid, "block_index": found["block_index"],   # the step, its transaction and its block
                              "block_hash": found["block_hash"], "confirmations": found["confirmations"]})   # with the block's hash and depth
                return issued                                                           # hand back any receipts

            conversion = self.on_ramp(sender, amount, bank_reference)                   # step 1: rand in, mZAR minted to the sender
            confirm("on-ramp (mint)", conversion["txid"])                               # mine it
            moved = self.transfer(sender, recipient, amount, "remittance")              # step 2: the sender pays the recipient
            confirm("transfer", moved["txid"])                                          # mine it
            receipt = None                                                              # there is no receipt unless the recipient redeems
            if redeem:                                                                  # step 3 is optional
                burned = self.off_ramp(recipient, amount)                               # the recipient burns the mZAR
                issued = confirm("off-ramp (burn)", burned["txid"])                     # mine it, which triggers the payout
                receipt = next(item for item in issued if item["txid"] == burned["txid"])   # the receipt for this burn
            payment = {                                                                 # the record of the whole payment
                "id": f"PAY-{len(self.payments) + 1:04d}",                              # a running identifier such as PAY-0001
                "sender": sender,                                                       # who paid in
                "recipient": recipient,                                                 # who was paid
                "amount": amount,                                                       # how much, in rand, mZAR and loti alike
                "status": "paid out" if redeem else "delivered as mZAR",                # where the value ended up
                "steps": steps,                                                         # the on-chain evidence for each step
                "conversion_id": conversion["id"],                                      # the on-ramp record
                "receipt_id": receipt["id"] if receipt else None,                       # the off-ramp receipt
            }
            self.payments.append(payment)                                               # keep the record
            return dict(payment)                                                        # return a copy

    # -----------------------------------------------------------------------
    # Reading the platform
    # -----------------------------------------------------------------------
    def reserve_report(self) -> dict:
        """Compare the money in reserve with the mZAR in circulation."""
        with self.lock:                                                                 # one consistent reading
            state = self.ledger.state()                                                 # the confirmed account table, replayed from the chain
            return {                                                                    # the proof-of-reserves report
                "rule": RESERVE_RULE,                                                   # the rule being tested
                "reserve_balance": self.reserve.balance,                                # rand held, from the cash book
                "reserve_currency": DEPOSIT_CURRENCY,                                   # ZAR
                "minted": state.minted,                                                 # mZAR ever created, from the chain
                "burned": state.burned,                                                 # mZAR ever destroyed, from the chain
                "supply": state.supply,                                                 # mZAR in circulation
                "asset": ASSET,                                                         # mZAR
                "fully_backed": self.reserve.balance >= state.supply,                   # the reserve rule holds when the reserve covers the supply
                "surplus": self.reserve.balance - state.supply,                         # rand held beyond the supply, for example a deposit not yet minted
                "entries": [dict(entry) for entry in self.reserve.entries],             # the cash book itself
            }

    def find_record(self, record_id: str):
        """Return the conversion or receipt with this identifier (or this transaction identifier), or None."""
        with self.lock:                                                                 # one consistent reading
            for record in self.conversions + self.receipts:                             # search both lists
                if record_id in (record["id"], record["txid"]):                         # matched by either identifier
                    return dict(record)                                                 # return a copy
            return None                                                                 # nothing matched

    def snapshot(self) -> dict:
        """Return everything the explorer needs in one consistent reading of the platform."""
        with self.lock:                                                                 # nothing may change while the reading is taken
            return {                                                                    # the snapshot
                "asset": ASSET,                                                         # the stablecoin
                "native_asset": NATIVE_ASSET,                                           # the mining-reward coin
                "difficulty": self.ledger.difficulty,                                   # the mining rule
                "genesis_hash": self.ledger.genesis_hash,                               # the fingerprint that identifies this network
                "reserve_agent": self.reserve_agent.address,                            # who may mint
                "chain": [block.to_dict() for block in self.ledger.chain],              # every block in its network form
                "pending": list(self.ledger.pending),                                   # a copy of the pending pool
                "wallets": [dict(record) for record in self.wallets.values()],          # every known wallet's public record
                "reserve": self.reserve_report(),                                       # the proof-of-reserves report
                "conversions": [dict(record) for record in self.conversions],           # every on-ramp record
                "receipts": [dict(record) for record in self.receipts],                 # every off-ramp receipt
            }
