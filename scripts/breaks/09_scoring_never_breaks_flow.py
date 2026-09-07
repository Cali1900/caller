TARGET = 'api/scorer.py'
EXPECT = 'test_a_scoring_failure_never_raises'
LABEL = 'let a scoring failure escape into the call flow'
OLD = """        except Exception as exc:
            failed += 1"""
NEW = """        except Exception as exc:
            raise
            failed += 1"""
