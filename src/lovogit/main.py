from argparse import ArgumentParser


def main(args):
    match args.mode:
        case "mode":
            pass
        case _:
            raise NotImplementedError("Incorrect mode")


if __name__ == "__main__":
    arg_parser = ArgumentParser()
    arg_parser.add_argument("mode")
    args = arg_parser.parse_args()
    main(args)
