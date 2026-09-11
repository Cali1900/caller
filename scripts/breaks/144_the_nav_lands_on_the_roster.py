# ⚠️ THE MENU MUST LAND ON THE SCREEN THAT GETS USED.
#
# /drips opened the SEQUENCE - the per-step rates and the copy editor - with the
# leads a link inside it. So seeing who is in a sequence meant going through a
# config screen first, every time. The sequence is set once; the roster is read
# daily, and the nav item should match that, the same way `Leads` is the call
# view's front door rather than a campaign's settings.
#
# One nav item, not two: "Drips" and "Drip leads" side by side would make somebody
# choose between two words for one subject on every visit.
TARGET = 'api/web.py'
EXPECT = 'test_the_nav_lands_on_the_roster_not_the_config'
LABEL = 'send /drips back to the sequence page instead of the roster'
OLD = """    return templates.TemplateResponse(request, 'drip_leads.html', {
        'hdr': hdr, 'c': camp, 'msg': msg, 'drips': drips,"""
NEW = """    return templates.TemplateResponse(request, 'drips.html', {
        'hdr': hdr, 'c': camp, 'msg': msg, 'drips': drips,"""
