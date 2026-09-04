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

Graphviz ships one of its layout engines at ``bin/fdp``, the same path as our
console script, and conda has no conflict detection for that: whichever
package links last owns the file. Anything pulling graphviz in transitively
(cmflib -> dvc -> pydot -> graphviz, i.e. the whole CMF provenance stack)
could therefore replace the FDP CLI with a graph layout tool.

Since 0.6.0 the recipe declares graphviz as a *run dependency* precisely so
link order puts us last and bare ``fdp`` keeps working; a packaged test guards
it. Renaming the console script remains the fallback if that ever stops
holding, and so far it has not been needed.

``python -m fdp`` resolves through the installed package rather than ``PATH``,
so it cannot be shadowed at all. Prefer it in scripts and documentation that
must run in environments carrying the provenance stack.
"""

import sys

from fdp.cli import main

if __name__ == "__main__":
    # argparse derives prog from basename(sys.argv[0]), which is
    # "__main__.py" under -m. Usage and error messages should show something
    # the reader can actually type.
    sys.argv[0] = "python -m fdp"
    main()
