#!/usr/bin/env bash

# Public deployment-operator actions. The implementing layer owns this contract;
# bin/fkst-ops derives both its usage and routing from this single declaration.
readonly FKST_OPS_PUBLIC_ACTIONS=(board status logs restart sync stop)
