"""harnessing_loop: the core of an agent.

One VLM, one loop, a set of tools, a sandbox, and gates that decide when
the job is done. Import the pieces you need or load a profile and run.

    from harnessing_loop import load_profile, Loop
    runtime = load_profile("chat").build(workspace="./work")
    loop = Loop(runtime)
    result = loop.run("Summarize the files in this folder.")

harnessing_loop。エージェントの中核。

1 つの VLM、1 つのループ、ツール一式、サンドボックス、そして仕事が終わったかを
判断するゲート。必要な部品を import するか、プロファイルを読み込んで実行する。
"""

from .core.loop import Loop
from .core.state import Terminal, Continue
from .profiles.base import Profile, Runtime, load_profile

__all__ = ["Loop", "Terminal", "Continue", "Profile", "Runtime", "load_profile"]
__version__ = "0.1.0"
