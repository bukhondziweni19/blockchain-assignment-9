"""
stablecoin.py
Assignment 9: Financial Blockchain Capstone (the stablecoin rules and the ledger)
Blockchain module (BLCH9X2), Master of Financial Engineering, University of Johannesburg

WHAT THIS FILE DOES
    Turns the SimpleBlockchain core (simple_blockchain.py) into a ledger for one
    simulated stablecoin, mZAR, where 1 mZAR represents 1 South African rand (ZAR)
    held in reserve.

        make_genesis        block 0, which carries the charter naming the reserve agent
        State               the account table: balances, sequence numbers and supply
        transaction_error   every rule a stablecoin transaction must pass
        replay              validates a whole chain from genesis and rebuilds the State
        Ledger              one node's chain plus its pool of pending transactions

    Three kinds of transaction move mZAR:

        mint       the reserve agent creates mZAR for a customer (the on-ramp)
        transfer   a holder sends mZAR to another address
        burn       a holder destroys mZAR, addressed to the reserve agent (the off-ramp)

THE ACCOUNT MODEL
    Balances are not stored in any block. They are worked out by replaying every
    transaction from genesis, so any party holding the chain reaches the same
    balances. Each address also has a sequence number, the count of transactions it
    has sent, and a transaction is valid only if it carries the next number. That
    stops a signed transaction being submitted a second time.

THE TWO ASSETS
    mZAR is created only by a mint and destroyed only by a burn. The mining reward is
    paid in a separate native coin, SBC, which cannot be transferred or redeemed and
    has no claim on the reserve. Mining therefore never changes the mZAR supply.

HOW TO RUN
    This file is a library and is not run directly. Run the demonstration with:
        python demo.py
"""

import copy                 # copy.deepcopy makes an independent duplicate of a list or dictionary
import re                   # re lets us check that an address has exactly the expected characters

from simple_blockchain import (     # the chain core carried forward from Assignments 1 to 7
    BLOCK_REWARD,                   # the native coins paid to the miner of each block
    COINBASE_SENDER,                # the sender name that marks a reward
    MIN_DIFFICULTY,                 # the lowest proof-of-work difficulty the network allows
    Block,                          # one block
    Blockchain,                     # the list of blocks with its integrity checks
    Transaction,                    # one transaction
    Wallet,                         # used here only for its signature check
    confirmations,                  # how many blocks confirm a transaction
    create_genesis_block,           # builds block 0
    make_coinbase,                  # builds the miner's reward
    mine_with_stats,                # the proof-of-work search
    pubkey_to_address,              # derives an address from a public key
    resolve_fork,                   # the fork-choice rule
    tx_id,                          # the identifier of a transaction
    validate_transaction,           # the structural checks from Assignment 3
)

# ===========================================================================
# CONFIGURATION
# ===========================================================================

ASSET = "mZAR"                          # the stablecoin: one unit represents one rand held in reserve
NATIVE_ASSET = "SBC"                    # SimpleBlockchain coin, the native unit that pays miners
RESERVE_RULE = "1 mZAR = 1 ZAR held in reserve"      # the one-for-one reserve rule, written into the genesis block
GENESIS_TIMESTAMP = 1_790_000_000       # the fixed genesis time used since Assignment 7, so every run shares one genesis
DEMO_DIFFICULTY = 4                     # leading zeros required of every block hash: about 16**4 = 65,536 hashes per block
KINDS = ("mint", "transfer", "burn")    # the only kinds of stablecoin transaction
MAX_AMOUNT = 1_000_000_000_000          # an upper limit on one transaction, so absurd values are refused early
MAX_REFERENCE_LENGTH = 64               # the longest reference a transaction may carry
ADDRESS_PATTERN = re.compile(r"[0-9a-f]{40}")   # an address is exactly 40 lowercase hexadecimal characters

# ===========================================================================
# SMALL CHECKS USED BY THE RULES
# ===========================================================================

def is_address(value) -> bool:
    """Return True if the value has the form of an address derived from a public key."""
    return isinstance(value, str) and ADDRESS_PATTERN.fullmatch(value) is not None   # text, and exactly 40 hexadecimal characters


def is_whole_number(value) -> bool:
    """Return True if the value is a whole number. True and False are excluded, although Python counts them as 1 and 0."""
    return isinstance(value, int) and not isinstance(value, bool)   # an integer that is not a boolean

# ===========================================================================
# THE GENESIS BLOCK AND ITS CHARTER
# ===========================================================================

def make_charter(issuer_address: str, timestamp: int) -> dict:
    """Build the charter: the genesis record that names the reserve agent, the only address allowed to mint."""
    charter = Transaction(                  # the charter follows the same schema as every other record on the chain
        sender=COINBASE_SENDER,             # it comes from the network's rules, not from an account
        recipient=issuer_address,           # the reserve agent who receives the authority to mint
        amount=0,                           # no value moves: the charter only grants authority
        timestamp=timestamp,                # the genesis time
        kind="charter",                     # marks this record as the charter
        asset=ASSET,                        # the asset the charter governs
        reference=RESERVE_RULE,             # the reserve rule in words, committed to by the genesis hash
    )
    return charter.to_dict()                # blocks store records as dictionaries


def make_genesis(issuer_address: str, difficulty: int = DEMO_DIFFICULTY) -> Block:
    """Build block 0. Its hash commits to the reserve agent's address and to the mining difficulty."""
    return create_genesis_block(                                        # the Assignment 2 genesis builder
        timestamp=GENESIS_TIMESTAMP,                                    # the fixed genesis time
        transactions=[make_charter(issuer_address, GENESIS_TIMESTAMP)], # the charter is the only record in block 0
        difficulty=difficulty,                                          # the difficulty every later block must declare and meet
    )


def charter_issuer(genesis: Block):
    """Read the reserve agent's address from a genesis block. Return None if the charter is missing or altered."""
    records = genesis.transactions                                          # everything block 0 carries
    if len(records) != 1 or not isinstance(records[0], dict):               # there must be exactly one record
        return None                                                         # anything else is not a valid charter
    issuer = records[0].get("recipient")                                    # the address the charter names
    if not is_address(issuer):                                              # it must have the form of an address
        return None                                                         # otherwise there is no usable reserve agent
    if records[0] != make_charter(issuer, genesis.timestamp):               # rebuild the only charter the rules allow for that address
        return None                                                         # any extra or altered field invalidates it
    return issuer                                                           # the reserve agent's address

# ===========================================================================
# THE ACCOUNT TABLE
# ===========================================================================

class State:
    """Who holds what. It is never stored in a block: it is rebuilt by replaying the chain."""

    def __init__(self, issuer: str):
        """Start with nobody holding anything."""
        self.issuer = issuer        # the reserve agent's address, read from the genesis charter
        self.balances = {}          # address -> mZAR held
        self.rewards = {}           # address -> SBC earned by mining
        self.sequences = {}         # address -> number of transactions that address has sent
        self.minted = 0             # total mZAR ever created
        self.burned = 0             # total mZAR ever destroyed
        self.seen = set()           # the identifier of every transaction already applied

    @property
    def supply(self) -> int:
        """Return the mZAR in circulation."""
        return self.minted - self.burned    # everything created minus everything destroyed

    def copy(self) -> "State":
        """Return an independent duplicate, so a trial change can be thrown away."""
        return copy.deepcopy(self)          # duplicate every table inside the state

    def apply(self, tx: dict) -> None:
        """Move the value of one transaction that has ALREADY passed transaction_error."""
        sender, recipient, amount = tx["sender"], tx["recipient"], tx["amount"]   # the three facts that move value
        if tx["kind"] == "mint":                                                # the reserve agent creates new mZAR
            self.balances[recipient] = self.balances.get(recipient, 0) + amount # credit the customer
            self.minted += amount                                               # the supply grows
        elif tx["kind"] == "transfer":                                          # a holder pays another address
            self.balances[sender] = self.balances.get(sender, 0) - amount       # debit the sender
            self.balances[recipient] = self.balances.get(recipient, 0) + amount # credit the recipient
        else:                                                                   # the only remaining kind is a burn
            self.balances[sender] = self.balances.get(sender, 0) - amount       # debit the holder, and credit nobody
            self.burned += amount                                               # the supply shrinks
        self.sequences[sender] = self.sequences.get(sender, 0) + 1              # the sender has now sent one more transaction
        self.seen.add(tx_id(tx))                                                # remember the identifier so it can never be applied again

# ===========================================================================
# THE RULES FOR ONE TRANSACTION
# ===========================================================================

def transaction_error(data, state: State):
    """Check one stablecoin transaction against the current state.

    Return None if it is valid, otherwise (code, message). The code is one of
    "malformed", "bad_signature", "replay", "unauthorised" or "insufficient_funds"."""
    # ----- shape: the Assignment 3 schema plus the capstone fields -----
    if not isinstance(data, dict):                                              # a transaction must be a set of named fields
        return "malformed", "a transaction must be a JSON object"               # JSON is JavaScript Object Notation
    try:                                                                        # a missing required field raises an error
        tx = Transaction.from_dict(data)                                        # read the fields into a Transaction
    except KeyError as missing:                                                 # sender, recipient or amount was absent
        return "malformed", f"the transaction is missing the field {missing}"   # name the missing field
    if tx.to_dict() != data:                                                    # writing it back out must give exactly what came in
        return "malformed", "the transaction has a missing timestamp, an unknown field or a field set to null"   # so nothing unsigned can ride along
    structurally_valid, reason = validate_transaction(tx)                       # the structural checks from Assignment 3
    if not structurally_valid:                                                  # for example an empty sender or a negative amount
        return "malformed", reason                                              # pass on the reason
    if tx.kind not in KINDS:                                                    # the kind decides which rules apply below
        return "malformed", "'kind' must be \"mint\", \"transfer\" or \"burn\"" # anything else is refused
    if tx.asset != ASSET:                                                       # this ledger moves one asset only
        return "malformed", f"'asset' must be \"{ASSET}\""                      # the native coin cannot be transferred
    if not is_whole_number(tx.amount) or tx.amount > MAX_AMOUNT:                # whole units only, so no rounding can ever occur
        return "malformed", f"'amount' must be a whole number from 1 to {MAX_AMOUNT:,}"   # decimals and absurd values are refused
    if not is_whole_number(tx.timestamp):                                       # whole seconds, so the signed text is identical on every machine
        return "malformed", "'timestamp' must be a whole number of seconds"     # decimals are refused
    if not is_whole_number(tx.sequence) or tx.sequence < 0:                     # the sender's transaction count
        return "malformed", "'sequence' must be a whole number, zero or more"   # it is required for every stablecoin transaction
    if not is_address(tx.sender) or not is_address(tx.recipient):               # both parties must be real addresses
        return "malformed", "'sender' and 'recipient' must be 40 character hexadecimal addresses"   # names are not accepted
    if tx.reference is not None and (not isinstance(tx.reference, str) or not tx.reference.isprintable()   # an optional reference must be printable text
                                     or not 1 <= len(tx.reference) <= MAX_REFERENCE_LENGTH):   # and short
        return "malformed", f"'reference' must be 1 to {MAX_REFERENCE_LENGTH} printable characters"   # explain the rule that failed
    if not isinstance(tx.public_key, str) or not isinstance(tx.signature, str):  # both are needed to check authorship
        return "malformed", "a 'public_key' and a 'signature' are required"     # an unsigned transaction is refused
    # ----- authorship: the Assignment 4 signature, now checked by the ledger itself -----
    try:                                                                        # the public key must be valid hexadecimal
        public_key_bytes = bytes.fromhex(tx.public_key)                         # convert it to raw bytes
    except ValueError:                                                          # it contained characters that are not hexadecimal
        return "malformed", "'public_key' must be hexadecimal text"             # so no address can be derived from it
    if pubkey_to_address(public_key_bytes) != tx.sender:                        # the key must be the one the sender's address was derived from
        return "bad_signature", "the public key does not belong to the sender's address"   # somebody else's key cannot spend this account
    if not Wallet.verify(tx.payload_bytes(), tx.signature, tx.public_key):      # the signature must cover these exact fields
        return "bad_signature", "the signature does not match the transaction's contents"   # a field was changed after signing, or the key is wrong
    # ----- replay protection -----
    if tx_id(data) in state.seen:                                               # the same signed instruction was applied before
        return "replay", "this transaction is already on the ledger"            # it cannot move value twice
    expected = state.sequences.get(tx.sender, 0)                                # the next sequence number this sender may use
    if tx.sequence != expected:                                                 # too low means reused, too high means a gap
        return "replay", f"'sequence' is {tx.sequence} but the sender's next sequence number is {expected}"   # say which number is expected
    # ----- authority: who may do what -----
    if tx.kind == "mint" and tx.sender != state.issuer:                         # only the address named in the genesis charter creates mZAR
        return "unauthorised", "only the reserve agent named in the genesis block may mint"   # anyone else is refused
    if tx.kind == "burn" and tx.recipient != state.issuer:                      # a burn is a redemption request to whoever holds the reserve
        return "unauthorised", "a burn must be addressed to the reserve agent"  # so it must name the reserve agent
    if tx.kind == "transfer" and tx.sender == tx.recipient:                     # paying yourself moves nothing
        return "malformed", "'sender' and 'recipient' must be different"        # so it is refused
    # ----- funds -----
    held = state.balances.get(tx.sender, 0)                                     # what the sender holds at this point
    if tx.kind in ("transfer", "burn") and held < tx.amount:                    # nobody may spend or burn more than they hold
        return "insufficient_funds", f"the sender holds {held:,} {ASSET} but the transaction needs {tx.amount:,}"   # say how much is short
    return None                                                                 # None means every rule passed

# ===========================================================================
# THE RULES FOR ONE BLOCK AND FOR A WHOLE CHAIN
# ===========================================================================

def block_rule_error(block: Block, state: State):
    """Check the transactions of one block. Return a message describing the first problem, or None.

    When the block is valid, state is updated to include it."""
    records = block.transactions                                                # everything the block carries
    if not records or not isinstance(records[0], dict) or records[0].get("sender") != COINBASE_SENDER:   # the Assignment 7 rule
        return "first transaction must be the coinbase"                         # every block starts with the miner's reward
    reward = records[0]                                                         # the reward
    if not is_address(reward.get("recipient")):                                 # it must be paid to a usable address
        return "coinbase recipient is not a valid address"                      # refuse a reward with a broken recipient
    if reward != make_coinbase(reward["recipient"], BLOCK_REWARD, block.timestamp, block.index):   # rebuild the only reward the rules allow
        return "coinbase is not the reward the rules allow"                     # a miner tried to overpay itself or reuse a reward
    working = state.copy()                                                      # work on a copy, so a rejected block changes nothing
    working.rewards[reward["recipient"]] = working.rewards.get(reward["recipient"], 0) + BLOCK_REWARD   # credit the miner in the native coin
    working.seen.add(tx_id(reward))                                             # remember the reward's identifier
    for record in records[1:]:                                                  # every other record must be a stablecoin transaction
        if isinstance(record, dict) and record.get("sender") == COINBASE_SENDER:   # a second reward in the same block
            return "extra coinbase (only one may create coins)"                 # is refused, as in Assignment 7
        problem = transaction_error(record, working)                            # apply every transaction rule, in block order
        if problem is not None:                                                 # one bad transaction invalidates the whole block
            return f"invalid transaction ({problem[1]})"                        # report which rule failed
        working.apply(record)                                                   # move the value before checking the next one
    state.__dict__.update(working.__dict__)                                     # the block is valid, so publish the new state
    return None                                                                 # None means the block passed every rule


def replay(blocks: list, genesis_hash=None, trail=None):
    """Validate a whole chain from genesis and rebuild the account table.

    Return (state, None) if the chain is valid, or (None, a message describing the first problem).
    If trail is a list, one line is added to it for every block that passed."""
    if not blocks or any(block is None for block in blocks):                    # an empty chain or an unreadable block is refused
        return None, "Chain is empty or holds an unreadable block."             # nothing further can be checked
    genesis = blocks[0]                                                         # block 0 sets the rules for the rest
    if genesis_hash is not None and genesis.hash != genesis_hash:               # the caller may insist on one particular genesis
        return None, "Genesis: does not match the expected genesis block."      # this chain belongs to a different network
    issuer = charter_issuer(genesis)                                            # who may mint, according to block 0
    if issuer is None:                                                          # the charter is missing or was altered
        return None, "Genesis: does not carry a valid stablecoin charter."      # so no mint on this chain can be trusted
    required = genesis.difficulty                                               # the difficulty block 0 declares for the network
    if not is_whole_number(required) or required < MIN_DIFFICULTY:             # it must be at least the Assignment 7 minimum
        return None, f"Genesis: declared difficulty must be a whole number of at least {MIN_DIFFICULTY}."   # refuse a network with no real work
    chain = Blockchain(genesis=genesis)                                         # wrap the blocks in the Assignment 5 class
    chain.chain = list(blocks)                                                  # so its integrity check can be reused
    intact, problems = chain.verify_chain_verbose(difficulty=MIN_DIFFICULTY)    # the four pillars: index, self-hash, link and proof of work
    if not intact:                                                              # a block was edited, moved or never mined
        return None, problems[0]                                                # report the first broken pillar
    state = State(issuer)                                                       # start from nobody holding anything
    if trail is not None:                                                       # the caller asked for a line per block
        trail.append({"block": 0, "hash": genesis.hash, "transactions": 1, "signatures": 0,   # block 0 holds the charter and no signatures
                      "result": f"charter names reserve agent {issuer}"})       # say what block 0 established
    for block in blocks[1:]:                                                    # every mined block, oldest first
        declared = block.difficulty                                             # the difficulty this block says it was mined to
        if not is_whole_number(declared) or declared < required:                # it may not be easier than the network's rule
            return None, f"Block {block.index}: declared difficulty {declared} is below the network's {required}."   # an under-mined block is refused
        if not block.hash.startswith("0" * declared):                           # and the hash must really show that much work
            return None, f"Block {block.index}: declared difficulty {declared} is not met by the hash."   # the declaration was false
        problem = block_rule_error(block, state)                                # the reward, signatures, sequence numbers and balances
        if problem is not None:                                                 # the first invalid block ends the replay
            return None, f"Block {block.index}: {problem}"                      # say which block failed and why
        if trail is not None:                                                   # the caller asked for a line per block
            trail.append({"block": block.index, "hash": block.hash, "transactions": len(block.transactions),   # how many records it holds
                          "signatures": len(block.transactions) - 1,            # every record except the reward carries a signature
                          "result": "hash, link, proof of work, reward, signatures, sequences and balances all hold"})   # what was checked
    return state, None                                                          # the chain is valid: hand back the rebuilt account table

# ===========================================================================
# THE LEDGER: ONE NODE'S CHAIN PLUS ITS PENDING POOL
# ===========================================================================

class Ledger:
    """The chain of blocks held by one node, and the transactions waiting to be mined."""

    def __init__(self, issuer_address: str, difficulty: int = DEMO_DIFFICULTY):
        """Start a ledger holding only the genesis block."""
        self.difficulty = difficulty                                            # the proof-of-work difficulty this node mines at
        self.blockchain = Blockchain(genesis=make_genesis(issuer_address, difficulty))   # the Assignment 5 chain, started from our genesis
        self.pending = []                                                       # transactions accepted but not yet mined, oldest first

    @property
    def chain(self) -> list:
        """Return the list of blocks, oldest first."""
        return self.blockchain.chain            # the list held by the Assignment 5 class

    @property
    def genesis_hash(self) -> str:
        """Return the hash of block 0, which identifies this network."""
        return self.chain[0].hash               # the fingerprint of the genesis block

    # -----------------------------------------------------------------------
    # Reading the ledger
    # -----------------------------------------------------------------------
    def state(self) -> State:
        """Replay the chain and return the confirmed account table."""
        state, problem = replay(self.chain)                         # this node's own chain is valid by construction
        if problem is not None:                                     # reaching here would mean the node's memory was corrupted
            raise RuntimeError(f"this node's chain is invalid: {problem}")   # stop loudly instead of serving wrong balances
        return state                                                # the confirmed balances, sequence numbers and supply

    def state_with_pending(self) -> State:
        """Return the account table as it will be once every pending transaction is mined."""
        state = self.state()                                        # start from the confirmed table
        for tx in self.pending:                                     # then apply the waiting transactions in arrival order
            state.apply(tx)                                         # each one passed the rules when it was accepted
        return state                                                # what the next block will produce

    def account(self, address: str) -> dict:
        """Report what one address holds and which sequence number it must use next."""
        confirmed, expected = self.state(), self.state_with_pending()   # the table now, and the table after the pending pool
        return {                                                    # a small report
            "address": address,                                     # whose account this is
            "asset": ASSET,                                         # the asset the balances are in
            "confirmed": confirmed.balances.get(address, 0),        # mZAR already mined into the chain
            "after_pending": expected.balances.get(address, 0),     # mZAR once the pending pool is mined
            "next_sequence": expected.sequences.get(address, 0),    # the number the address must put on its next transaction
            "mining_rewards": confirmed.rewards.get(address, 0),    # SBC earned by mining, which is not redeemable
        }

    def find_transaction(self, txid: str):
        """Look a transaction up by identifier. Return where it is, or None if this node has never seen it."""
        for block in self.chain:                                                # search the mined blocks first
            for position, tx in enumerate(block.transactions):                  # and every record inside each
                if tx_id(tx) == txid:                                           # found it in the chain
                    return {                                                    # report where it was mined
                        "txid": txid,                                           # the identifier that was asked for
                        "status": "confirmed",                                  # it is part of the chain
                        "block_index": block.index,                             # the block that holds it
                        "block_hash": block.hash,                               # that block's fingerprint
                        "position_in_block": position,                          # its place in the block, where 0 is the reward
                        "confirmations": confirmations(self.chain, txid),       # k = H - h + 1, from Assignment 7
                        "transaction": tx,                                      # the transaction itself
                    }
        for tx in self.pending:                                                 # otherwise search the pending pool
            if tx_id(tx) == txid:                                               # found it waiting
                return {"txid": txid, "status": "pending", "block_index": None, "block_hash": None,   # it has no block yet
                        "position_in_block": None, "confirmations": 0, "transaction": tx}             # and no confirmations
        return None                                                             # this node has never seen that identifier

    # -----------------------------------------------------------------------
    # Changing the ledger
    # -----------------------------------------------------------------------
    def submit(self, data) -> tuple:
        """Try to place a signed transaction in the pending pool. Return (code, message, txid)."""
        problem = transaction_error(data, self.state_with_pending())    # the same rules a block is checked against
        if problem is not None:                                         # the transaction broke a rule
            return problem[0], problem[1], None                         # say which, and store nothing
        self.pending.append(copy.deepcopy(data))                        # keep an independent copy in the pool
        return "accepted", "transaction accepted into the pending pool", tx_id(data)   # report success and the identifier

    def mine(self, miner_address: str, timestamp: int) -> tuple:
        """Mine one block holding the reward and every pending transaction. Return (block, attempts, seconds)."""
        index = self.blockchain.tip().index + 1                                 # the new block takes the next position
        reward = make_coinbase(miner_address, BLOCK_REWARD, timestamp, index)   # the miner's reward, in the native coin
        block = self.blockchain.make_next_block([reward] + self.pending, timestamp=timestamp)   # the reward first, then the pool in arrival order
        block.difficulty = self.difficulty                                      # declare the difficulty, as in Assignment 7
        attempts, seconds = mine_with_stats(block, self.difficulty)             # the proof-of-work search from Assignment 6
        self.blockchain.append_block(block)                                     # the Assignment 5 gates: index, link and self-hash
        self.pending = []                                                       # its transactions are no longer pending
        return block, attempts, seconds                                         # report the block and what it cost

    def consider_chain(self, blocks: list) -> tuple:
        """Compare another chain with ours and adopt it if the fork-choice rule says so. Return (adopted, message)."""
        _, problem = replay(blocks, self.genesis_hash)                          # validate before comparing, as in Assignment 7
        if problem is not None:                                                 # an invalid chain never wins, whatever its length
            return False, f"the other chain is invalid ({problem})"             # say why it was refused
        winner = resolve_fork([self.chain, blocks],                             # the two candidates
                              is_valid=lambda chain: replay(chain, self.genesis_hash)[1] is None,   # validity includes every stablecoin rule
                              current=self.chain)                               # ties keep the chain this node saw first
        if winner[-1].hash == self.blockchain.tip().hash:                       # our own chain won or tied
            return False, "the other chain is not longer"                       # nothing changes
        on_winner = {tx_id(tx) for block in winner for tx in block.transactions}   # every identifier on the chain being adopted
        returned = [tx for block in self.chain for tx in block.transactions[1:] # transactions from the chain being abandoned
                    if tx_id(tx) not in on_winner]                              # that the new chain does not already hold
        candidates = returned + self.pending                                    # they go back to the pool ahead of what was already waiting
        self.blockchain.chain = winner                                          # adopt the longer valid chain
        self.pending = []                                                       # rebuild the pool from nothing
        for tx in candidates:                                                   # offer each candidate back in its original order
            self.submit(tx)                                                     # the normal rules drop anything no longer valid
        return True, "adopted the longer valid chain"                           # report the switch
