"""Compatibility for: cd client && python -m unittest test_trackpad_gestures."""
if not __package__:
    import bootstrap
    bootstrap.setup()


def load_tests(loader, suite, pattern):
    # Root discovery finds the canonical tests/ tree; do not run them twice.
    if pattern is not None:
        return suite
    return loader.loadTestsFromName("tests.common.test_legacy_gestures")
