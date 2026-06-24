"""D1: real CLI-agent worker check."""
from decima import cli_worker


def run(k, line):
    line("\n== CLI WORKER (subprocess principal + receipt) ==")
    cap_id = cli_worker.integrate(k)
    assert cap_id, "CLI worker capability was not integrated"

    transcript = k.say("delegate codex-shim as CliWorker: codex-shim: review auth module")
    for ln in transcript:
        line("  " + ln)

    w = k.weave()
    tasks = [
        t for t in w.of_type("task")
        if t.content.get("worker_name") == "CliWorker"
        and t.content.get("capability") == "codex-shim"
    ]
    assert tasks, "delegated CLI worker task was not recorded"
    task = tasks[-1]
    assert task.content["status"] == "done", task.content
    worker = w.get(task.content["worker"])
    grant = w.get(task.content["grant"])
    assert worker.content["principal"] != k.decima.id, "worker did not get its own principal"
    assert grant.content["caveats"]["budget"] <= 10, grant.content

    receipt_id = task.content.get("result")
    receipt = w.get(receipt_id)
    assert receipt is not None and receipt.type == "result", "task result is not a receipt Cell"
    assert receipt.content["status"] == "SUCCEEDED", receipt.content
    assert receipt.content["cap"] == "codex-shim", receipt.content
    assert receipt.content["effect_class"] == "READ", receipt.content
    assert receipt.content["code"] == 0, receipt.content
    assert "codex-shim reviewed: review auth module" in receipt.content["out"], receipt.content

    tree_hit = any("CliWorker" in ln and "codex-shim" in ln and "[done]" in ln
                   for ln in k.task_tree())
    assert tree_hit, "CLI worker missing from task tree"

    line(f"  receipt {receipt.id[:8]} status={receipt.content['status']} "
         f"code={receipt.content['code']} sandbox={receipt.content['sandbox']['mode']}")
    line("  sandbox seam: subprocess allowlist now; landlock/seccomp or microVM later")
