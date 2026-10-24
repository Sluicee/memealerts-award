import sys

if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        from memeaward.smoke import main
    else:
        from memeaward.desktop import main
    main()
