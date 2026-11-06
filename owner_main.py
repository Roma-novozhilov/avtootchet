import sys

if __name__ == "__main__":
    if "--stock-report" in sys.argv:
        # утренний отчёт по складу без окна (планировщик Windows)
        from avtootchet.owner.stock_job import run_morning
        sys.exit(run_morning())
    from avtootchet.owner.app import main
    main()
