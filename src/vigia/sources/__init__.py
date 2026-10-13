"""Registry of source adapters, keyed by the name used in the config."""

from vigia.sources.ats import Ashby, Greenhouse, Lever
from vigia.sources.boards import Himalayas, Jobicy, Remotive
from vigia.sources.getonbrd import GetOnBoard
from vigia.sources.hn import HNWhoIsHiring
from vigia.sources.linkedin import LinkedIn

REGISTRY = {cls.name: cls for cls in
            (GetOnBoard, Greenhouse, Ashby, Lever, Remotive, Himalayas, Jobicy,
             HNWhoIsHiring, LinkedIn)}
