import sys

if any(a in sys.argv for a in ("--collect", "--serve", "--demo")):
    # задачи Windows: без окна и без Qt
    from avtootchet.vitrina.runner import main

    sys.exit(main(sys.argv))

from avtootchet.vitrina.app import main  # noqa: E402

if __name__ == "__main__":
    main()
