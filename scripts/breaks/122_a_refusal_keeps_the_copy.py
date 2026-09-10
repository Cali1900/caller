# ⚠️ A REFUSAL THAT DESTROYS THE DRAFT IS STILL DATA LOSS.
#
# The sequence save refused correctly and then REDIRECTED, which re-rendered the
# steps from the database - so the step being complained about disappeared,
# copy and all. The operator retypes a step to find out what was wrong with it.
#
# And the reason was unreadable: the save redirects to #drip, the page banner
# sits ~90 lines above that anchor, so the browser scrolled straight past the
# explanation. A step vanishing with no visible error is indistinguishable from
# a silent drop, which is how it was reported.
#
# Re-rendering with the POSTED rows fixes both: the work comes back, and the
# reason renders inside the sequence card where the eye already is.
TARGET = 'api/web.py'
EXPECT = 'test_a_refused_save_gives_back_the_copy_that_was_typed'
LABEL = 'redirect a refusal instead of re-rendering the typed copy'
OLD = """        return _campaign_view(request, campaign_id, steps=_posted_steps(rows),
                              seq_msg=f'REJECTED: {exc}')"""
NEW = """        msg = f'REJECTED: {exc}'
        return RedirectResponse(
            f'/campaign/{campaign_id}?msg={urllib.parse.quote(msg)}#drip',
            status_code=303)"""
