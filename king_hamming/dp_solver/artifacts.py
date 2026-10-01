"""Portable KHD1 run-length DP artifacts compatible with the preserved reference format."""

from __future__ import annotations

import hashlib
from typing import Any


# Validate dimensions independently of native array or solver representations.
def dimensions(p: int, r: int) -> tuple[int, int, int]:
    """Return q,F,B for a supported nontrivial odd prime power; reject malformed inputs."""

    if type(p) is not int or type(r) is not int or p<2 or p>1621 or r<3 or r>31 or r%2==0 or any(p%d==0 for d in range(2,int(p**0.5)+1)):
        raise ValueError("invalid DP prime or extension degree")
    q=p**r
    f=p**(r//2)
    budget=p*f
    if q>2**64-1 or budget>2**32-1 or (budget+1)**2*12>2**64-1:
        raise ValueError("DP field or budget exceeds native integer width")
    return q,f,budget


# Check feasibility and gain using run counts, without expanding a giant split.
def validate(document: dict[str, Any]) -> None:
    """Validate dimensions, maximal run compression, budgets and gain; this does not prove optimality."""

    p,r=document["p"],document["r"]
    q,f,budget=dimensions(p,r)
    if (document.get("q"),document.get("f"),document.get("budget"))!=(q,f,budget):
        raise ValueError("DP dimensions mismatch")
    runs=document["runs"]
    theta=document["theta"]
    if type(theta) is not int or not 0<theta<2**64 or not isinstance(runs,list) or not 0<len(runs)<=budget:
        raise ValueError("invalid DP score or run count")
    used_u=used_v=gain=cosets=0
    previous=None
    for run in runs:
        if not isinstance(run,dict) or set(run)!={"a","b","t","repeat"}:
            raise ValueError("invalid DP run fields")
        a,b,t,repeat=(run[key] for key in ("a","b","t","repeat"))
        if any(type(value) is not int for value in (a,b,t,repeat)) or not all(1<=value<=p for value in (a,b,t)) or not 1<=repeat<=budget:
            raise ValueError("invalid DP run values")
        if previous==(a,b,t):
            raise ValueError("adjacent identical runs are not compressed")
        previous=(a,b,t)
        used_u+=a*t*repeat
        used_v+=b*t*repeat
        cosets+=t*repeat
        gain+=t*len({(h*t-g)%p for g in range(a) for h in range(b)})*repeat
    if max(used_u,used_v)>budget or gain!=theta or cosets+1>q-1:
        raise ValueError("DP split budgets or gain are inconsistent")


# Use canonical little-endian base-128 varints, with fixed uint64 public bounds.
def varint(value: int) -> bytes:
    """Encode value as an unsigned canonical varint, rejecting out-of-range inputs."""

    if type(value) is not int or not 0<=value<2**64:
        raise ValueError("DP integer exceeds uint64")
    encoded=bytearray()
    while value>=128:
        encoded.append((value&127)|128)
        value>>=7
    encoded.append(value)
    return bytes(encoded)


# Encode only mathematical inputs and run-length choices, then checksum the entire payload.
def encode_dp(document: dict[str, Any]) -> bytes:
    """Return validated portable KHD1 bytes; no native table or field state is included."""

    validate(document)
    payload=bytearray(b"KHD1")
    for value in (document["p"],document["r"],document["theta"],len(document["runs"])):
        payload.extend(varint(value))
    for run in document["runs"]:
        for key in ("a","b","t","repeat"):
            payload.extend(varint(run[key]))
    return bytes(payload)+hashlib.sha256(payload).digest()


# Check checksum and canonical encoding before parsing or accepting split feasibility.
def decode_dp(raw: bytes) -> dict[str, Any]:
    """Return a KHDP2-draft-shaped document from KHD1; reject corruption, padding and invalid runs."""

    if not 40<=len(raw)<=16*1024**2 or raw[:4]!=b"KHD1" or hashlib.sha256(raw[:-32]).digest()!=raw[-32:]:
        raise ValueError("invalid KHD1 size, magic or checksum")
    payload=raw[:-32]
    position=4

    def integer() -> int:
        """Consume one bounded canonical varint from payload or fail on truncation/overflow."""

        nonlocal position
        value=0
        for shift in range(0,64,7):
            if position>=len(payload):
                raise ValueError("truncated KHD1 integer")
            byte=payload[position]
            position+=1
            if shift==63 and byte>1:
                raise ValueError("KHD1 integer overflow")
            value|=(byte&127)<<shift
            if byte<128:
                if shift and byte==0:
                    raise ValueError("noncanonical KHD1 integer")
                return value
        raise ValueError("KHD1 integer overflow")

    p,r,theta,count=(integer() for _ in range(4))
    q,f,budget=dimensions(p,r)
    if not 0<count<=budget or count>(len(payload)-position)//4:
        raise ValueError("invalid KHD1 run count")
    runs=[dict(zip(("a","b","t","repeat"),(integer() for _ in range(4)))) for _ in range(count)]
    if position!=len(payload):
        raise ValueError("unexpected KHD1 trailing bytes")
    document={"format":"KHDP2-draft","p":p,"r":r,"q":q,"f":f,"budget":budget,"theta":theta,"runs":runs}
    validate(document)
    return document
