"""Read exact Polygon CTF getters for selected research observations."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx

CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
RPC = "https://polygon-bor-rpc.publicnode.com"
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("roots", type=Path, nargs="+")
    a = p.parse_args()
    semaphore = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=20) as client:

        async def rpc(method, params):
            async with semaphore:
                r = await client.post(
                    RPC, json=dict(jsonrpc="2.0", id=1, method=method, params=params)
                )
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data["result"]

        chain = int(await rpc("eth_chainId", []), 16)
        assert chain == 137
        block = await rpc("eth_blockNumber", [])
        for root in a.roots:
            for path in sorted(root.glob("*/observation.json")):
                obj = json.loads(path.read_text())
                if obj["status"] != "SELECTED":
                    continue
                target = path.with_name("ctf_finality.json")
                if target.exists():
                    continue
                cid = obj["trade"]["condition_id"]
                assert len(cid) == 66 and cid.startswith("0x")
                calls = ["0xdd34de67" + cid[2:]] + [
                    "0x0504c814" + cid[2:] + format(i, "064x") for i in range(2)
                ]
                before = time.time()
                try:
                    values = await asyncio.gather(
                        *(rpc("eth_call", [dict(to=CTF, data=data), block]) for data in calls)
                    )
                    row = dict(
                        source=RPC,
                        condition=cid,
                        contract=CTF,
                        chain_id=chain,
                        block=block,
                        denominator=int(values[0], 16),
                        numerators=[int(v, 16) for v in values[1:]],
                    )
                except Exception as exc:
                    row = dict(error=repr(exc), condition=cid, source=RPC)
                row.update(
                    requested=before,
                    received=time.time(),
                    code_sha256=SOURCE_SHA256,
                    observation_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
                target.write_text(json.dumps(row, indent=2))
                print(
                    json.dumps(
                        dict(
                            file=str(target),
                            denominator=row.get("denominator"),
                            numerators=row.get("numerators"),
                            error=row.get("error"),
                        )
                    )
                )


if __name__ == "__main__":
    asyncio.run(main())
