import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env", override=False)

import wandb
import weave
from openai import OpenAI

client = OpenAI()

# Weave automatically tracks the inputs, outputs and code of this function
@weave.op()
def extract_dinos(sentence: str) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": """In JSON format extract a list of `dinosaurs`, with their `name`,
their `common_name`, and whether its `diet` is a herbivore or carnivore"""
            },
            {
                "role": "user",
                "content": sentence
            }
            ],
            response_format={ "type": "json_object" }
        )
    return response.choices[0].message.content

# Initializes Weave, and sets the team and project to log data to
weave.init('traces-quickstart')

sentence = """I watched as a Tyrannosaurus rex (T. rex) chased after a Triceratops (Trike), \
both carnivore and herbivore locked in an ancient dance. Meanwhile, a gentle giant \
Brachiosaurus (Brachi) calmly munched on treetops, blissfully unaware of the chaos below."""

### 追加する差分 ###
import wandb
from wandb_xrpl_proof import IncrementalAnchor
# weave.init() の後に追加
run = wandb.init(project="traces-quickstart")

# ループをwithブロックで囲む
with IncrementalAnchor(run, chunk_size=2) as anchor:
    for i in range(5):
        result, call = extract_dinos.call(sentence)  # .call() に変更
        print(f"Run {i+1}: {result}")
        anchor.log({"step": i}, weave_call=call)

for tx in anchor.tx_hashes:
    print(f"XRPL testnet: https://testnet.xrpl.org/transactions/{tx}")

# proof ファイルを保存（verify_demo.py --chain で使用）
import json
proof = {
    "final_tx_hash": anchor.tx_hashes[-1],
    "chunk_hashes": anchor.chunk_hashes,
    "tx_hashes": anchor.tx_hashes,
    "wandb_run_url": run.url,
    "samples": 5,
    "chunk_size": 2,
}
Path("weave_proof.json").write_text(json.dumps(proof, indent=2))
print("Proof saved: weave_proof.json")

run.finish()