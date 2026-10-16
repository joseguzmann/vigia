"""Registry of source adapters, keyed by the name used in the config."""

from vigia.sources.ats import Ashby, Greenhouse, Lever
from vigia.sources.ats_more import Breezy, Recruitee, SmartRecruiters, Teamtailor, Workable
from vigia.sources.boards import Himalayas, Jobicy, RemoteOK, Remotive, WeWorkRemotely, WorkingNomads
from vigia.sources.ecuador import Computrabajo, Multitrabajos
from vigia.sources.getonbrd import GetOnBoard
from vigia.sources.hn import HNWhoIsHiring
from vigia.sources.linkedin import LinkedIn
from vigia.sources.linkedin_posts import LinkedInPosts
from vigia.sources.strider import Strider
from vigia.sources.workday import Workday

REGISTRY = {cls.name: cls for cls in
            (GetOnBoard, Multitrabajos, Computrabajo, Greenhouse, Ashby, Lever, Workday, Teamtailor, Workable, SmartRecruiters, Breezy,
             Recruitee, Remotive, Himalayas, Jobicy, RemoteOK, HNWhoIsHiring, Strider, WorkingNomads,
             WeWorkRemotely, LinkedIn, LinkedInPosts)}
