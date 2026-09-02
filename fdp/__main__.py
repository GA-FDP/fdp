# Copyright 2026 General Atomics
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Make the FDP CLI reachable as ``python -m fdp``.

The ``fdp`` console script is not always reachable. Graphviz installs its
force-directed layout engine at the same path -- ``bin/fdp``, one of the eight
engines it ships -- and conda has no conflict detection for that: ``conda-meta``
records graphviz as the owner and graphviz wins. Any environment containing
``cmflib`` pulls graphviz transitively (cmflib -> dvc -> pydot -> graphviz), so
in those environments ``fdp run`` silently invokes a graph layout tool.

``python -m fdp`` resolves through the installed package rather than ``PATH``,
so it works regardless. Prefer it in scripts and documentation that must run in
environments carrying the provenance stack.

See ``docs/2026-09-02-fdp-cli-rename.md`` in the workspace for the full
analysis; a rename of the console script is planned for a major release.
"""

import sys

from fdp.cli import main

if __name__ == "__main__":
    # argparse derives prog from basename(sys.argv[0]), which is
    # "__main__.py" under -m. Usage and error messages should show something
    # the reader can actually type.
    sys.argv[0] = "python -m fdp"
    main()
