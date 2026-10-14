"""Registry of source adapters, keyed by the name used in the config."""

from vigia.sources.ats import Ashby, Greenhouse, Lever
from vigia.sources.boards import Himalayas, Jobicy, Remotive, WeWorkRemotely, WorkingNomads
from vigia.sources.getonbrd import GetOnBoard
from vigia.sources.hn import HNWhoIsHiring
from vigia.sources.linkedin import LinkedIn
from vigia.sources.linkedin_posts import LinkedInPosts
from vigia.sources.strider import Strider

REGISTRY = {cls.name: cls for cls in
            (GetOnBoard, Greenhouse, Ashby, Lever, Remotive, Himalayas, Jobicy,
             HNWhoIsHiring, Strider, WorkingNomads, WeWorkRemotely, LinkedIn, LinkedInPosts)}
