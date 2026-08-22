# Known gaps

The publication lock remains a blocking `LOCK_EX` without a timeout or filesystem fallback. Owner
exit releases `flock`; a waiter timeout would not recover a hung owner or restore transaction state
and would require a separate failure and retry contract.
