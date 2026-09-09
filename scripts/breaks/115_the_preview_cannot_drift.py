# THE PREVIEW MUST GO THROUGH THE SAME render() THE REAL SENDS USE.
#
# A preview that substitutes placeholders its own way is a SECOND implementation
# of the rules, and the first time the two disagree it is lying about what goes
# out. That is worse than having no preview, because a preview is what the copy
# gets judged on.
#
# This break echoes the raw template instead of rendering it - exactly what a
# naive client-side preview would show: {{first_name}} where a name should be.
# The named test compares the endpoint's output against drafts.render() directly.
TARGET = 'api/web.py'
EXPECT = 'test_the_preview_uses_the_same_render_the_real_sends_use'
LABEL = 'preview the raw template instead of rendering it'
OLD = """        subject = drafts_mod.render(form.get(f'subject_{i}') or '', vals)
        body = drafts_mod.render(form.get(f'body_{i}') or '', vals)"""
NEW = """        subject = form.get(f'subject_{i}') or ''
        body = form.get(f'body_{i}') or ''"""
