# Known gaps

## Bypassed-checkout contamination diagnostic

When the checkout containing `bin/fkst-ops` does not match the active pin, that checkout cannot
reliably diagnose its own contamination: the selected pinned implementation may predate any such
diagnostic. A future stable, pin-independent bootstrap layer must reject and report candidate
contamination before selecting the pinned implementation. This is owned by the `fkst-ops` bootstrap
maintainers. Checkout-local warning coverage is intentionally omitted because it cannot model the
production version-skew case.

The publication lock remains a blocking `LOCK_EX` without a timeout or filesystem fallback. Owner
exit releases `flock`; a waiter timeout would not recover a hung owner or restore transaction state
and would require a separate failure and retry contract.
