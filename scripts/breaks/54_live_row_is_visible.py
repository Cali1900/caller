# The picker is a SCROLLING list. Without scrolling the live row into view, a
# different row sits at the top with its radio checked and the panel reads as
# "that one is live" when it is not - which is exactly the confusion the radio
# buttons were introduced to remove. Sean reported the picker as broken on
# this alone; the backend had been correct the whole time.
TARGET = 'api/templates/campaign.html'
EXPECT = 'test_the_page_scrolls_the_live_row_into_view'
LABEL = 'stop scrolling the LIVE prompt row into view'
OLD = """  var live = box.querySelector('tr.islive');
  if (live) live.scrollIntoView({block: 'center'});"""
NEW = """  var live = box.querySelector('tr.islive');"""
