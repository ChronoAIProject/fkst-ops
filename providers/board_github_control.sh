#!/usr/bin/env bash
# Generic adapter to a producer-owned GitHub control fact tool.
set -uo pipefail
fail() {
  python3 -c 'import json,sys; print(json.dumps({"version":"fkst.ops.invocation.v1","ok":False,"failure":{"code":sys.argv[1],"message":sys.argv[2],"details":{}}},separators=(",",":")))' "$1" "$2"
  exit "$3"
}
request=$(cat) || fail INVALID_INPUT "unable to read invocation" 2
tool=$(printf '%s' "$request" | python3 -c '
import json,os,sys
try: r=json.load(sys.stdin); i=r["input"]
except (KeyError,TypeError,json.JSONDecodeError): raise SystemExit(2)
if r.get("version")!="fkst.ops.invocation.v1" or r.get("contract")!="fkst.ops.board.github-control.v1": raise SystemExit(2)
if not isinstance(i.get("platform_checkout"),str) or not isinstance(i.get("profile"),dict): raise SystemExit(2)
p=i["profile"].get("fact_tool")
if not isinstance(p,str) or not p or os.path.isabs(p) or ".." in p.split("/"): raise SystemExit(3)
print(i["platform_checkout"]+"/"+p)
')
case $? in 2) fail INVALID_INPUT "input violates github-control contract" 2;; 3) fail PRODUCER_FAILED "profile does not select a safe package-owned fact_tool" 1;; esac
[ -x "$tool" ] || fail PRODUCER_FAILED "package-owned GitHub control fact tool is unavailable" 1
printf '%s' "$request" | exec "$tool"
