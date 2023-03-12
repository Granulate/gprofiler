from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from gprofiler.gprofiler_types import ProcessToStackSampleCounters, StackToSampleCount
from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)


def parse_one_collapsed(collapsed: str, add_comm: Optional[str] = None) -> StackToSampleCount:
    """
    Parse a stack-collapsed listing.

    If 'add_comm' is not None, add it as the first frame for each stack.
    """
    stacks: StackToSampleCount = Counter()

    for line in collapsed.splitlines():
        if line.strip() == "":
            continue
        if line.startswith("#"):
            continue
        try:
            stack, _, count = line.rpartition(" ")
            if add_comm is not None:
                stacks[f"{add_comm};{stack}"] += int(count)
            else:
                stacks[stack] += int(count)
        except Exception:
            logger.exception(f'bad stack - line="{line}"')

    return stacks


def parse_one_collapsed_file(collapsed: Path, add_comm: Optional[str] = None) -> StackToSampleCount:
    """
    Parse a stack-collapsed file.
    """
    return parse_one_collapsed(collapsed.read_text(), add_comm)


def parse_many_collapsed(text: str) -> ProcessToStackSampleCounters:
    """
    Parse a stack-collapsed listing where stacks are prefixed with the command and pid/tid of their
    origin.
    """
    results: ProcessToStackSampleCounters = defaultdict(Counter)
    bad_lines = []

    for line in text.splitlines():
        try:
            stack, count = line.rsplit(" ", maxsplit=1)
            comm_pid_tid, stack = stack.split(";", maxsplit=1)
            comm, pid_tid = comm_pid_tid.rsplit("-", maxsplit=1)
            pid = int(pid_tid.split("/")[0])
            results[pid][f"{comm};{stack}"] += int(count)
        except ValueError:
            bad_lines.append(line)

    if bad_lines:
        logger.warning(f"Got {len(bad_lines)} bad lines when parsing (showing up to 8):\n" + "\n".join(bad_lines[:8]))

    return results
