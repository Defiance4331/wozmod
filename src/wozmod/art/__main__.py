import sys

if sys.argv[1:2] == ["compose"]:
    from .compose import main
    del sys.argv[1]
elif sys.argv[1:2] == ["render"]:
    from .render import main
    del sys.argv[1]
else:
    from .build import main

raise SystemExit(main())
