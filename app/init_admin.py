"""Create the single administrator explicitly; never at API startup."""

import argparse
import getpass
from pathlib import Path
import re
import sys
import warnings

from argon2.exceptions import HashingError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import Admin
from app.passwords import hash_password


class AdminAlreadyExists(Exception):
    pass


class SecretSafeParser(argparse.ArgumentParser):
    def error(self, message):
        # Unknown arguments might accidentally contain a password.
        self.exit(2, "Arguments invalides ; utiliser --help.\n")


def validate_credentials(username, password):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", username):
        raise ValueError("Identifiant : 1 à 100 lettres ASCII, chiffres, points, tirets ou underscores.")
    if not 15 <= len(password) <= 128 or "\n" in password or "\r" in password:
        raise ValueError("Mot de passe : 15 à 128 caractères sur une seule ligne, sans transformation.")


def initialize_admin(session, username, password):
    """Commit one admin; never update an existing account or reset its password."""
    validate_credentials(username, password)
    if session.scalar(select(Admin.id).limit(1)) is not None:
        raise AdminAlreadyExists("Un administrateur existe déjà ; aucun changement effectué.")
    admin = Admin(username=username, password_hash=hash_password(password), active=True)
    session.add(admin)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise AdminAlreadyExists("Un administrateur existe déjà ; aucun changement effectué.") from None
        raise
    return admin.id


def read_password_file(path):
    # Read a bounded amount and remove only one optional final line terminator.
    with Path(path).open("rb") as file:
        value = file.read(1025)
    if len(value) > 1024:
        raise ValueError("Fichier de secret trop volumineux.")
    password = value.decode("utf-8")
    if password.endswith("\r\n"):
        password = password[:-2]
    elif password.endswith("\n"):
        password = password[:-1]
    return password


def main(argv=None):
    parser = SecretSafeParser(description="Initialiser l’unique administrateur CardioScope.", allow_abbrev=False)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-file", help="Chemin d’un secret UTF-8, jamais le mot de passe en argument.")
    args = parser.parse_args(argv)
    try:
        if args.password_file:
            password = read_password_file(args.password_file)
        else:
            if not sys.stdin.isatty():
                raise ValueError("Terminal interactif requis, ou fournir --password-file.")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("Mot de passe : ")
                confirmation = getpass.getpass("Confirmer : ")
            if password != confirmation:
                raise ValueError("La confirmation ne correspond pas.")
        # Check before opening a connection, avoiding SQL for malformed input.
        validate_credentials(args.username, password)
        with Session(get_engine(), expire_on_commit=False) as session:
            initialize_admin(session, args.username, password)
    except (ValueError, OSError, UnicodeError, AdminAlreadyExists) as error:
        # Do not echo paths, secret contents or operating-system exception details.
        message = str(error) if isinstance(error, (ValueError, AdminAlreadyExists)) and not isinstance(error, UnicodeError) else "Impossible de lire le fichier de secret UTF-8."
        print(message, file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Base indisponible ou schéma non initialisé ; vérifier la configuration et les migrations.", file=sys.stderr)
        return 1
    except (HashingError, getpass.GetPassWarning):
        print("Hachage ou saisie masquée indisponible ; initialisation annulée.", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Initialisation annulée.", file=sys.stderr)
        return 1
    print("Compte administrateur créé. Aucun secret n’est affiché.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
