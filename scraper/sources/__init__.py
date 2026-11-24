"""Source registry: maps `source` in companies.yaml to list/detail functions.

To add a career site that doesn't fit an existing adapter, write a generator
`fetch(company) -> Iterable[Job]` (and optionally `detail(company, job) -> dict`)
and register it here.
"""
from . import bigtech, custom, global_ats, simple_ats, tw_electronics, tw_semi, workday

SOURCES = {
    "jibe": (global_ats.jibe, None),
    "oracle": (global_ats.oracle, global_ats.oracle_detail),
    "radancy": (global_ats.radancy, global_ats.radancy_detail),
    "apple": (global_ats.apple, None),
    "asml": (global_ats.asml, None),
    "ibm": (global_ats.ibm, None),
    "foxconn": (tw_electronics.foxconn, tw_electronics.foxconn_detail),
    "wistron": (tw_electronics.wistron, None),
    "twm": (tw_electronics.twm, tw_electronics.twm_detail),
    "synology": (tw_electronics.synology, None),
    "cht": (tw_electronics.cht, tw_electronics.cht_detail),
    "fet": (tw_electronics.fet, None),
    "mediatek": (tw_semi.mediatek, None),
    "realtek": (tw_semi.realtek, tw_semi.realtek_detail),
    "umc": (tw_semi.umc, tw_semi.umc_detail),
    "successfactors": (tw_semi.successfactors, tw_semi.successfactors_detail),
    "talentsite": (tw_semi.talentsite, tw_semi.talentsite_detail),
    "manatal": (custom.manatal, None),
    "jazzhr": (custom.jazzhr, custom.jazzhr_detail),
    "esun": (custom.esun, None),
    "wix_cms": (custom.wix_cms, None),
    "yourator": (custom.yourator, custom.yourator_detail),
    "sea": (custom.sea, None),
    "line": (custom.line, custom.line_detail),
    "workday": (workday.fetch, workday.detail),
    "greenhouse": (simple_ats.greenhouse, None),
    "lever": (simple_ats.lever, None),
    "ashby": (simple_ats.ashby, None),
    "smartrecruiters": (simple_ats.smartrecruiters, simple_ats.smartrecruiters_detail),
    "eightfold": (bigtech.eightfold, bigtech.eightfold_detail),
    "google": (bigtech.google, None),
    "amazon": (bigtech.amazon, None),
}
