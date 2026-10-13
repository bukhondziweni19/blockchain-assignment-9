"""
simple_blockchain.py
Assignment 9: Financial Blockchain Capstone (the chain core)
Blockchain module (BLCH9X2), Master of Financial Engineering, University of Johannesburg

WHAT THIS FILE DOES
    Carries the group's SimpleBlockchain build forward from Assignments 1 to 7, in the
    same order as the Assignment 7 notebook:

        Section 1  Assignment 1   SHA-256 (Secure Hash Algorithm, 256-bit) helper
        Section 2  Assignment 2   Block, genesis block, linking
        Section 3  Assignment 3   Transaction and its structural validation
        Section 4  Assignment 4   Wallet: ECDSA (Elliptic Curve Digital Signature Algorithm) keys and signatures
        Section 5  Assignment 5   Blockchain: appending blocks and checking the chain
        Section 6  Assignment 6   Mining (proof-of-work) and the coinbase reward
        Section 7  Assignment 7   Transaction identifiers, confirmations and the fork-choice rule

    The stablecoin rules, the reserve and the payment API (Application Programming
    Interface) are built on top of this file in stablecoin.py, remittance.py and
    payment_api.py.

CAPSTONE EXTENSIONS TO THE EARLIER CODE
    Each one is marked "CAPSTONE" where it appears. All are backward compatible: a
    block or transaction that does not use the new fields hashes exactly as before.

        Transaction   five optional fields (kind, asset, sequence, public_key, reference),
                      included in the dictionary and in the signed bytes only when set
        Wallet        from_seed() for repeatable demonstration keys, and deterministic
                      signatures, so a demonstration prints the same identifiers every run
        Block         from_dict(), so a block can be rebuilt from its network form
        make_coinbase the reward is stamped with the block's time and number
        resolve_fork  the validity test is passed in, so the stablecoin rules are applied
                      before chains are compared

HOW TO RUN
    This file is a library and is not run directly. Run the demonstration with:
        python demo.py
"""

import copy                 # copy.deepcopy makes an independent duplicate of a list or dictionary
import hashlib              # hashlib provides SHA-256, the fingerprint function used throughout
import json                 # json turns Python dictionaries into text in a controlled, repeatable way
import time                 # time measures how long mining takes and supplies default timestamps

from ecdsa import BadSignatureError, SECP256k1, SigningKey, VerifyingKey   # the elliptic curve tools: keys, the Bitcoin curve and the bad-signature error
from ecdsa.util import sigdecode_der, sigencode_der                        # read and write signatures in DER (Distinguished Encoding Rules) form

# ===========================================================================
# SECTION 1: CRYPTOGRAPHY (ASSIGNMENT 1)
# ===========================================================================

def sha256_string(text: str) -> str:
    """Return the SHA-256 fingerprint of a piece of text, written as 64 hexadecimal characters."""
    encoded_text = text.encode("utf-8")               # convert the text into raw bytes, because hashing works on bytes
    return hashlib.sha256(encoded_text).hexdigest()   # hash the bytes and return the result as readable text

# ===========================================================================
# SECTION 2: BLOCK (ASSIGNMENT 2)
# ===========================================================================

GENESIS_PREVIOUS_HASH = "0" * 64   # 64 zeros, the agreed convention meaning "there is no earlier block"


class Block:
    """One block in the chain: a batch of transactions, sealed with a fingerprint."""

    def __init__(self, index, timestamp, transactions, previous_hash, nonce=0, difficulty=None):
        """Create a block and immediately seal it by computing its hash."""
        self.index = index                                  # the position of this block in the chain, starting at 0 for genesis
        self.timestamp = int(timestamp)                     # when the block was created, as a Unix timestamp in whole seconds
        self.transactions = copy.deepcopy(transactions)     # an independent copy of the transactions, so later edits elsewhere cannot reach the block
        self.previous_hash = previous_hash                  # the fingerprint of the block before this one, which is what forms the chain
        self.nonce = nonce                                  # the "number used once", the only field a miner changes while searching
        self.difficulty = difficulty                        # Assignment 7: the number of leading zeros this block declares it was mined to
        self.hash = self.compute_hash()                     # seal the block by fingerprinting everything above

    def payload_for_hash(self) -> dict:
        """Collect every field the hash must commit to, leaving out the hash itself."""
        payload = {                                 # the five fields from Assignment 2
            "index": self.index,                    # committing to position stops a block being moved elsewhere in the chain
            "timestamp": self.timestamp,            # committing to time stops the block being back-dated
            "transactions": self.transactions,      # committing to the transactions is what makes the amounts tamper evident
            "previous_hash": self.previous_hash,    # committing to the parent is what links the chain together
            "nonce": self.nonce,                    # committing to the nonce is what makes the mining work count
        }
        if self.difficulty is not None:             # Assignment 7: the difficulty is hashed only when it was declared
            payload["difficulty"] = self.difficulty # so blocks from Assignments 2 to 6 keep the hashes they always had
        return payload                              # hand back the fields to be hashed

    def compute_hash(self) -> str:
        """Recompute this block's fingerprint from its current contents."""
        raw = json.dumps(self.payload_for_hash(), sort_keys=True, separators=(",", ":"))   # one agreed text form: sorted keys and no optional spaces
        return sha256_string(raw)                                                          # fingerprint that text with SHA-256

    def to_dict(self) -> dict:
        """Return the block as a plain dictionary, the form in which it travels over the network."""
        return {**self.payload_for_hash(), "hash": self.hash}   # the hashed fields plus the stored hash, so a reader can check it

    @staticmethod
    def from_dict(data):
        """CAPSTONE: rebuild a block from its network form. Return None if the data is unusable."""
        try:                                                    # data from outside cannot be trusted to have the right shape
            block = Block(                                      # build the block from the hashed fields
                index=int(data["index"]),                       # the claimed position, forced to a whole number
                timestamp=int(data["timestamp"]),               # the claimed time, forced to a whole number
                transactions=list(data["transactions"]),        # the claimed transactions, forced to a list
                previous_hash=str(data["previous_hash"]),       # the claimed parent hash, forced to text
                nonce=int(data["nonce"]),                       # the claimed nonce, forced to a whole number
                difficulty=data.get("difficulty"),              # the declared difficulty, or None if the block declares none
            )
            block.hash = str(data["hash"])                      # keep the hash the sender CLAIMED, so it can be checked later
            return block                                        # hand back the rebuilt block
        except (KeyError, TypeError, ValueError):               # a missing field or a wrong type lands here
            return None                                         # None tells the caller the data was unusable

    def __repr__(self):
        """Control what Python prints when a block is inspected."""
        return f"Block(index={self.index}, hash={self.hash[:10]}...)"   # the position and the start of the hash are enough to recognise it


def create_genesis_block(timestamp=None, transactions=None, difficulty=None) -> Block:
    """Return block 0, the only block that has no parent."""
    return Block(                                       # build the block
        index=0,                                        # genesis always sits at position 0
        timestamp=timestamp or int(time.time()),        # the supplied time, or the current time if none was given
        transactions=transactions or [],                # CAPSTONE: genesis may carry records, such as the stablecoin's charter
        previous_hash=GENESIS_PREVIOUS_HASH,            # 64 zeros, because there is no earlier block
        difficulty=difficulty,                          # CAPSTONE: genesis may declare the difficulty every later block must meet
    )


def create_linked_block(previous: Block, transactions, timestamp=None, nonce=0) -> Block:
    """Return a new block that points at the block before it."""
    return Block(                                       # build the block
        index=previous.index + 1,                       # one position after its parent
        timestamp=timestamp or int(time.time()),        # the supplied time, or the current time if none was given
        transactions=transactions,                      # the transactions this block carries
        previous_hash=previous.hash,                    # the link: the parent's fingerprint is stored inside the child
        nonce=nonce,                                    # the starting nonce, normally 0 before mining
    )


def is_hash_valid(block: Block) -> bool:
    """Return True if the block's stored hash still matches its contents."""
    return block.hash == block.compute_hash()   # any edit after sealing changes the recomputed hash, so the two differ

# ===========================================================================
# SECTION 3: TRANSACTIONS (ASSIGNMENT 3)
# ===========================================================================

EXTENSION_FIELDS = ("kind", "asset", "sequence", "public_key", "reference")   # CAPSTONE: the optional fields added to the five from Assignment 3


class Transaction:
    """One payment instruction: sender pays recipient an amount at a moment in time."""

    def __init__(self, sender, recipient, amount, timestamp=None, signature=None, kind=None, asset=None, sequence=None, public_key=None, reference=None):
        """Store the five Assignment 3 fields and the optional capstone fields."""
        self.sender = sender                                                    # the address of the paying party
        self.recipient = recipient                                              # the address of the receiving party
        self.amount = amount                                                    # the value being moved
        self.timestamp = timestamp if timestamp is not None else time.time()    # when the instruction was created (now, if not supplied)
        self.signature = signature                                              # the sender's signature as hexadecimal text, or None before signing
        self.kind = kind                                                        # CAPSTONE: "mint", "transfer" or "burn"
        self.asset = asset                                                      # CAPSTONE: which asset is moved, for example "mZAR"
        self.sequence = sequence                                                # CAPSTONE: how many transactions this sender has made before this one
        self.public_key = public_key                                            # CAPSTONE: the sender's public key, so anyone can check the signature
        self.reference = reference                                              # CAPSTONE: a short reference linking the transaction to an off-chain record

    def to_dict(self) -> dict:
        """Return the transaction as a plain dictionary."""
        data = {                                    # the five fields from Assignment 3, always present
            "sender": self.sender,                  # who is paying
            "recipient": self.recipient,            # who is being paid
            "amount": self.amount,                  # how much
            "timestamp": self.timestamp,            # when it was created
            "signature": self.signature,            # the authorisation
        }
        for field in EXTENSION_FIELDS:              # CAPSTONE: the optional fields
            value = getattr(self, field)            # read the field from this object by name
            if value is not None:                   # a field that was never set is left out
                data[field] = value                 # so a five-field transaction looks exactly as it did in Assignment 3
        return data                                 # hand back the dictionary

    @classmethod
    def from_dict(cls, data):
        """Rebuild a transaction from a dictionary."""
        return cls(                                 # build a new Transaction
            data["sender"],                         # the paying party (required)
            data["recipient"],                      # the receiving party (required)
            data["amount"],                         # the value (required)
            data.get("timestamp"),                  # the creation time
            data.get("signature"),                  # the signature, if there is one
            **{field: data.get(field) for field in EXTENSION_FIELDS},   # CAPSTONE: the optional fields, each None when absent
        )

    def payload_bytes(self) -> bytes:
        """Return the exact bytes a wallet signs: every field except the signature itself."""
        body = {                                    # the four fields signed in Assignment 4
            "amount": self.amount,                  # the value
            "recipient": self.recipient,            # who is being paid
            "sender": self.sender,                  # who is paying
            "timestamp": self.timestamp,            # when it was created
        }
        for field in EXTENSION_FIELDS:              # CAPSTONE: the optional fields are signed too
            value = getattr(self, field)            # read the field from this object by name
            if value is not None:                   # only fields that were set
                body[field] = value                 # so nobody can change the kind, asset, sequence or reference after signing
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")   # the agreed text form, converted to bytes

    def __repr__(self):
        """Control what Python prints when a transaction is inspected."""
        return f"Transaction(sender={self.sender}, recipient={self.recipient}, amount={self.amount})"   # the three facts that identify a payment


def validate_transaction(tx):
    """Check the shape of a transaction. Return (True, "OK") or (False, the reason)."""
    if not tx.sender or not isinstance(tx.sender, str):                 # the sender must be non-empty text
        return False, "Sender must be a non-empty string"               # otherwise no payer is identified
    if not tx.recipient or not isinstance(tx.recipient, str):           # the recipient must be non-empty text
        return False, "Recipient must be a non-empty string"            # otherwise no beneficiary is identified
    if isinstance(tx.amount, bool) or not isinstance(tx.amount, (int, float)) or tx.amount <= 0:   # CAPSTONE adds the bool test: Python treats True as 1
        return False, "Amount must be positive (>0)"                    # zero, negative and non-numeric amounts are refused
    if isinstance(tx.timestamp, bool) or not isinstance(tx.timestamp, (int, float)):   # the creation time must be a number
        return False, "Timestamp must be numeric"                       # otherwise transactions cannot be ordered in time
    if tx.signature is not None and not isinstance(tx.signature, str):  # a signature, when present, must be text
        return False, "Signature must be a string or None"              # anything else breaks the schema
    return True, "OK"                                                   # every structural rule passed

# ===========================================================================
# SECTION 4: WALLETS AND DIGITAL SIGNATURES (ASSIGNMENT 4)
# ===========================================================================

def pubkey_to_address(pubkey_bytes: bytes) -> str:
    """Derive an address from a public key: the first 40 hexadecimal characters of its SHA-256 hash."""
    return hashlib.sha256(pubkey_bytes).hexdigest()[:40]   # short enough to read, long enough that two keys never share one


class Wallet:
    """A key pair plus its derived address. Whoever holds the private key controls the address."""

    def __init__(self, private_key: SigningKey, name: str = ""):
        """Build a wallet around an existing private key."""
        self.name = name                                                # a human readable label, used only for display
        self.private_key = private_key                                  # the secret half of the pair: it signs, and must never be shared
        self.public_key = private_key.get_verifying_key()               # the public half, computed from the private key and never the reverse
        self.public_key_hex = self.public_key.to_string().hex()         # the public key as hexadecimal text, safe to publish
        self.address = pubkey_to_address(self.public_key.to_string())   # the address other parties use to pay this wallet

    @classmethod
    def create(cls, name: str = "") -> "Wallet":
        """Create a wallet with a fresh random key. This is the method to use for any real key."""
        private_key = SigningKey.generate(curve=SECP256k1)   # draw a random private key on SECP256k1, the curve Bitcoin uses
        return cls(private_key, name=name)                   # wrap it in a wallet

    @classmethod
    def from_seed(cls, seed: str, name: str = "") -> "Wallet":
        """CAPSTONE: create a wallet whose key is derived from a text seed, so a demonstration is repeatable.

        Anyone who knows the seed can rebuild the private key, so this is for demonstrations only."""
        digest = hashlib.sha256(seed.encode("utf-8")).digest()                      # hash the seed into 32 bytes
        exponent = int.from_bytes(digest, "big") % (SECP256k1.order - 1) + 1        # turn the bytes into a valid private key number, never zero
        private_key = SigningKey.from_secret_exponent(exponent, curve=SECP256k1)    # build the private key from that number
        return cls(private_key, name=name)                                          # wrap it in a wallet

    def sign(self, message: bytes) -> str:
        """Sign a message with the private key. Return the signature as hexadecimal text."""
        signature = self.private_key.sign_deterministic(   # CAPSTONE: the deterministic method of RFC 6979 (an internet standard)
            message,                                       # the bytes being signed
            hashfunc=hashlib.sha256,                       # the message is hashed with SHA-256 before signing
            sigencode=sigencode_der,                       # write the signature in DER form, as in Assignment 4
        )                                                  # the same key and message always give the same signature, and no random number can leak the key
        return signature.hex()                             # hexadecimal text survives being placed in JSON (JavaScript Object Notation)

    @staticmethod
    def verify(message: bytes, sig_hex: str, pubkey_hex: str) -> bool:
        """Check a signature against a message and a public key. No private key is needed."""
        try:                                                                                    # malformed input raises an error rather than returning False
            public_key = VerifyingKey.from_string(bytes.fromhex(pubkey_hex), curve=SECP256k1)   # rebuild the public key from its hexadecimal text
            signature = bytes.fromhex(sig_hex)                                                  # convert the signature back into raw bytes
            return public_key.verify(signature, message, hashfunc=hashlib.sha256, sigdecode=sigdecode_der)   # True only if this key signed these exact bytes
        except (BadSignatureError, ValueError, TypeError, AssertionError):                      # a wrong signature or unusable input lands here
            return False                                                                        # either way the signature is not accepted

    def sign_transaction(self, tx: Transaction) -> None:
        """Sign a transaction in place, filling in its signature field."""
        tx.signature = self.sign(tx.payload_bytes())   # sign the agreed bytes and store the result on the transaction

    def __repr__(self):
        """Control what Python prints when a wallet is inspected. The private key is never shown."""
        return f"Wallet(name={self.name!r}, address={self.address})"   # the label and address only

# ===========================================================================
# SECTION 5: CHAIN INTEGRITY (ASSIGNMENT 5)
# ===========================================================================

class Blockchain:
    """An ordered list of blocks, each pointing at the one before it."""

    def __init__(self, genesis=None):
        """Start a chain from the supplied genesis block, or from a new one."""
        self.chain = [genesis if genesis is not None else create_genesis_block()]   # the list of blocks, oldest first

    def tip(self) -> Block:
        """Return the newest block, which the next block must point at."""
        return self.chain[-1]   # "[-1]" means the last item in a list

    def append_block(self, block: Block) -> None:
        """Add a block to the chain, refusing it if it does not fit on the tip."""
        tip = self.tip()                                                                                    # the block the new one must extend
        if block.index != tip.index + 1:                                                                    # gate 1: positions must count up by one
            raise ValueError(f"Block {block.index}: index must be {tip.index + 1} to extend the tip.")      # the block is out of sequence
        if block.previous_hash != tip.hash:                                                                 # gate 2: the block must point at the tip
            raise ValueError(f"Block {block.index}: previous_hash does not match the tip's hash.")          # the link is broken
        if not is_hash_valid(block):                                                                        # gate 3: the stored hash must match the contents
            raise ValueError(f"Block {block.index}: stored hash does not match its own computed hash.")     # something was edited after sealing
        self.chain.append(block)                                                                            # all three gates passed, so the block joins the chain

    def make_next_block(self, transactions, timestamp=None, nonce: int = 0) -> Block:
        """Build (but do not append) the block that would follow the tip."""
        return create_linked_block(self.tip(), transactions, timestamp=timestamp, nonce=nonce)   # linked to the current tip

    def verify_chain_verbose(self, difficulty=None):
        """Check the pillars of chain integrity. Return (True, []) or (False, a list of problems)."""
        problems = []                                                           # one readable message per broken rule
        if not self.chain:                                                      # an empty chain cannot be valid
            return False, ["Chain is empty."]                                   # nothing further can be checked
        genesis = self.chain[0]                                                 # block 0 has rules of its own
        if genesis.index != 0:                                                  # the first block must sit at position 0
            problems.append(f"Genesis: index pillar failed (index={genesis.index}, expected 0).")   # record the failure
        if genesis.previous_hash != GENESIS_PREVIOUS_HASH:                      # and must point at the 64 zero sentinel
            problems.append("Genesis: previous_hash sentinel failed (expected 64 zeros).")          # record the failure
        if not is_hash_valid(genesis):                                          # and its stored hash must match its contents
            problems.append("Genesis: self-hash pillar failed (stored hash does not match the contents).")   # record the failure
        prefix = ("0" * difficulty) if difficulty else None                     # the proof-of-work pattern to look for, if one was asked for
        for position in range(1, len(self.chain)):                              # every later block is checked against its parent
            current, previous = self.chain[position], self.chain[position - 1]  # the block and the one before it
            if current.index != position:                                       # pillar 1: the index must equal the position in the list
                problems.append(f"Block {position}: index pillar failed (index={current.index}, expected {position}).")   # record the failure
            if not is_hash_valid(current):                                      # pillar 2: the stored hash must match the contents
                problems.append(f"Block {position}: self-hash pillar failed (stored={current.hash[:12]}..., recomputed={current.compute_hash()[:12]}...).")   # record the failure
            if current.previous_hash != previous.hash:                          # pillar 3: the block must point at its parent
                problems.append(f"Block {position}: link pillar failed (previous_hash={current.previous_hash[:12]}..., block {position - 1}.hash={previous.hash[:12]}...).")   # record the failure
            if prefix and not current.hash.startswith(prefix):                  # pillar 4: the hash must show proof of work
                problems.append(f"Block {position}: difficulty pillar failed (hash {current.hash[:12]}... lacks {difficulty} leading zeros).")   # record the failure
        return len(problems) == 0, problems                                     # valid only when no problem was found

# ===========================================================================
# SECTION 6: MINING AND PROOF-OF-WORK (ASSIGNMENT 6)
# ===========================================================================

BLOCK_REWARD = 50       # the fixed number of native coins created for the miner of each block
COINBASE_SENDER = "NETWORK"   # the sender name that marks a reward: nobody is debited, the coins are new


def mine_with_stats(block: Block, difficulty: int):
    """Try nonce values one after another until the block hash starts with enough zeros.

    Return (number of hashes tried, seconds taken)."""
    target = "0" * difficulty                       # the pattern we demand, for example "0000" when difficulty is 4
    block.nonce = 0                                 # every search starts at zero, which makes the result repeatable
    block.hash = block.compute_hash()               # hash the block with that first nonce
    attempts = 1                                    # one hash has been tried so far
    started = time.perf_counter()                   # start a stopwatch so the cost of mining can be reported
    while not block.hash.startswith(target):        # keep going until the hash starts with enough zeros
        block.nonce += 1                            # move to the next nonce
        block.hash = block.compute_hash()           # hash the block again with the new nonce
        attempts += 1                               # count this attempt
    elapsed = time.perf_counter() - started         # stop the stopwatch
    return attempts, elapsed                        # report how many hashes it took and how long


def make_coinbase(miner_address: str, amount=BLOCK_REWARD, timestamp=None, block_index=None) -> dict:
    """Build the special first transaction of a block, which pays the miner in newly created native coins."""
    reference = None if block_index is None else f"reward for block {block_index}"   # CAPSTONE: naming the block makes every reward unique
    reward = Transaction(                           # the reward follows the same schema as every other transaction
        sender=COINBASE_SENDER,                     # no real sender: these coins did not exist before
        recipient=miner_address,                    # the miner's address
        amount=amount,                              # the fixed block reward
        timestamp=timestamp,                        # CAPSTONE: the block's own time, so the reward is identical on every run
        reference=reference,                        # CAPSTONE: which block this reward belongs to
    )
    return reward.to_dict()                         # blocks store transactions as dictionaries

# ===========================================================================
# SECTION 7: CONSENSUS HELPERS (ASSIGNMENT 7)
# ===========================================================================

MIN_DIFFICULTY = 2      # the network rule from Assignment 7: every mined block needs at least 2 leading zeros


def tx_id(tx: dict) -> str:
    """Return the identifier of a transaction: the SHA-256 hash of the bytes that were signed.

    The signature itself is left out, so re-encoding a signature can never change the identifier."""
    return sha256_string(Transaction.from_dict(tx).payload_bytes().decode("utf-8"))   # fingerprint the signed fields


def confirmations(blocks: list, txid: str) -> int:
    """Return how many blocks confirm a transaction: k = H - h + 1, or 0 if it is not on this chain."""
    height = blocks[-1].index                                       # H, the index of the newest block
    for block in blocks:                                            # search every block
        if any(tx_id(tx) == txid for tx in block.transactions):     # is the transaction inside this one?
            return height - block.index + 1                         # its own block counts as the first confirmation
    return 0                                                        # the transaction is not on this chain


def chain_work(blocks: list, rule: str = "length") -> int:
    """Score a chain: by its number of blocks ("length") or by its cumulative proof-of-work ("work")."""
    if rule == "length":                                            # the longest-chain rule
        return len(blocks)                                          # the score is simply the number of blocks
    if rule == "work":                                              # the heaviest-chain rule
        return sum(16 ** (block.difficulty if block.difficulty is not None else MIN_DIFFICULTY)   # each extra zero makes a block 16 times harder
                   for block in blocks[1:])                         # summed over every mined block (genesis is not mined)
    raise ValueError(f"unknown rule {rule!r}")                      # any other rule name is a programming mistake


def resolve_fork(chains: list, is_valid, rule: str = "length", current=None) -> list:
    """Choose between competing chains: validate first, then compare. Return a copy of the winner.

    CAPSTONE: is_valid is supplied by the caller, so the stablecoin rules decide validity."""
    valid = [chain for chain in chains if is_valid(chain)]              # an invalid chain never wins, whatever its length
    if not valid:                                                       # every candidate broke a rule
        raise ValueError("No valid chain to choose from.")              # there is nothing to adopt
    best = max(chain_work(chain, rule) for chain in valid)              # the highest score among the valid chains
    tied = [chain for chain in valid if chain_work(chain, rule) == best]   # every chain that reaches that score
    if current is not None:                                             # the first-seen tie-break from Assignment 7
        for chain in tied:                                              # look for the chain this node already holds
            if chain[-1].hash == current[-1].hash:                      # the node's own chain is among the best
                return copy.deepcopy(chain)                             # so it keeps the chain it saw first
    winner = min(tied, key=lambda chain: chain[-1].hash)                # otherwise the smallest tip hash wins, so the choice is deterministic
    return copy.deepcopy(winner)                                        # an independent copy, so the caller cannot alter the original
