TARGET = 'api/web.py'
EXPECT = 'test_a_hostile_company_name_is_escaped_not_executed'
LABEL = 'turn off HTML autoescaping (transcripts are other people\'s text)'
OLD = "templates = Jinja2Templates(directory='api/templates')"
NEW = ("templates = Jinja2Templates(directory='api/templates')\n"
       "templates.env.autoescape = False")
