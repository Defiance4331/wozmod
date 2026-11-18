"""What keeps a step's output independent of its worker count: wozmod.workers.ordered_map, and step code fanning out
through nothing else."""

import ast
import time

from wozmod import resources
from wozmod.install import manifest
from wozmod.workers import ordered_map

# What starts workers of its own (a pool, a process, a thread), or gives each worker state of its own.
FAN_OUT_MODULES = {"concurrent", "multiprocessing", "_thread"}
FAN_OUT_NAMES = {("threading", "Thread"), ("threading", "Timer"), ("threading", "local"), ("os", "fork"),
                 ("os", "forkpty"), ("os", "register_at_fork")}


def _fan_outs(name: str, tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"{name}:{node.lineno} import {a.name}" for a in node.names
                      if a.name.split(".")[0] in FAN_OUT_MODULES]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            root = (node.module or "").split(".")[0]
            found += [f"{name}:{node.lineno} from {node.module} import {a.name}" for a in node.names
                      if root in FAN_OUT_MODULES or (node.module, a.name) in FAN_OUT_NAMES]
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
              and (node.value.id, node.attr) in FAN_OUT_NAMES):
            found.append(f"{name}:{node.lineno} {node.value.id}.{node.attr}")
    return found


def test_step_code_fans_out_only_through_ordered_map():
    # Bug: a step's module (any module in a step's closure, or its installer function) hands work to a pool, thread
    # or process of its own, and joins the results as they finish, or gives each worker state of its own; its output
    # then depends on the player's core count, and two players with the same step key hold different files.
    root = resources.root()
    found = []
    for step in manifest.manifests(root).values():
        for module in step.modules:
            if module != "wozmod.workers" and (path := manifest.source_file(root, module)):
                found += _fan_outs(module, ast.parse(path.read_text(encoding="utf-8")))
        installer = ast.parse(manifest.source_file(root, "wozmod.install").read_text(encoding="utf-8"))
        found += [line for node in installer.body
                  if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in step.installer
                  for line in _fan_outs(f"wozmod.install.{node.name}", node)]
    assert not found, "fan out through wozmod.workers.ordered_map:\n" + "\n".join(sorted(set(found)))


def test_items_that_finish_out_of_order_are_joined_in_hand_out_order():
    # Bug: items that finish out of order are joined in finishing order, so at 4 workers a step writes its pieces
    # in another order than at 1, and its output depends on the worker count.
    items = list(range(8))

    def slow_first(item: int) -> str:
        time.sleep(0.02 * (len(items) - item))  # the earlier an item is handed out, the later it finishes
        return f"piece {item}"

    finished = {}
    for jobs in (1, 4):
        finished[jobs] = []
        joined = list(ordered_map(slow_first, items, jobs, finished[jobs].append))
        assert joined == [f"piece {item}" for item in items], jobs
    assert finished[1] == items and finished[4] != items  # at 4 workers they did finish out of order
