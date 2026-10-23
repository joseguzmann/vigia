"""Registry of source adapters, keyed by the name used in the config."""

from vigia.sources.ats import Ashby, Greenhouse, Lever
from vigia.sources.ats_more import BambooHR, Breezy, Manatal, Rippling, Recruitee, SmartRecruiters, Teamtailor, Workable
from vigia.sources.boards import Himalayas, Jobicy, RemoteOK, Remotive, WeWorkRemotely, WorkingNomads
from vigia.sources.discover import Discover
from vigia.sources.ecuador import Computrabajo, Multitrabajos
from vigia.sources.freehire import FreeHire
from vigia.sources.getonbrd import GetOnBoard
from vigia.sources.gmail import Gmail
from vigia.sources.hn import HNWhoIsHiring
from vigia.sources.indeed import Indeed
from vigia.sources.linkedin import LinkedIn
from vigia.sources.linkedin_posts import LinkedInPosts
from vigia.sources.linkedin_posts_apify import LinkedInPostsApify
from vigia.sources.linkedin_session import LinkedInSession
from vigia.sources.openroles import OpenRoles
from vigia.sources.remoteyeah import RemoteYeah
from vigia.sources.strider import Strider
from vigia.sources.torre import Torre
from vigia.sources.workday import Workday

REGISTRY = {cls.name: cls for cls in
            (GetOnBoard, Torre, Multitrabajos, Computrabajo, Indeed, Greenhouse, Ashby, Lever, Workday, Teamtailor, Workable, SmartRecruiters, Breezy,
             Recruitee, BambooHR, Rippling, Manatal, Remotive, Himalayas, Jobicy, RemoteOK, HNWhoIsHiring, Strider, WorkingNomads,
             WeWorkRemotely, RemoteYeah, FreeHire, LinkedIn, LinkedInPosts, LinkedInPostsApify, LinkedInSession, Gmail, Discover, OpenRoles)}
