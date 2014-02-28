from __future__ import print_function
import re
import optparse
import MySQLdb as mdb
import getpass
import sys
import contextlib
import ConfigParser
from multiprocessing import Pool
from shardgather.renderers import RENDERERS, DEFAULT_RENDERER


DEFAULT_POOLSIZE = 5


def highlight(sql):
    from pygments import highlight as pygments_highlight
    from pygments.lexers import SqlLexer
    from pygments.formatters import TerminalFormatter

    return pygments_highlight(sql, SqlLexer(), TerminalFormatter())


def query(conn, sql):
    with contextlib.closing(conn.cursor()) as cursor:
        cursor.execute(sql)
        return cursor.fetchall()


def get_shard_databases(hostname, username, password, is_shard_db):
    with contextlib.closing(
        mdb.connect(hostname, username, password)
    ) as conn:
        try:
            return [
                db_name for (db_name,) in query(conn, 'SHOW DATABASES')
                if is_shard_db(db_name)
            ]
        except mdb.Error as e:
            print(str(e))


def collect((sql, hostname, username, password, db_name)):
    print("Running on %s" % db_name)
    with contextlib.closing(mdb.connect(
        hostname, username, password,
        db=db_name, cursorclass=mdb.cursors.DictCursor)
    ) as conn:
        try:
            query(conn, "USE %s" % db_name)
            collected = query(conn, sql % dict(db_name=db_name))
            print("%d rows returned for %s" % (len(collected), db_name))
            return db_name, collected
        except mdb.Error as e:
            print(str(e))


def aggregate(current_aggregated, next):
    db_name, collected = next
    current_aggregated[db_name] = collected
    return current_aggregated


def configure():
    parser = optparse.OptionParser()
    parser.add_option(
        '-c', '--config', dest='config_file_name',
        help='Config file', metavar='PATH_TO_CONFIG_FILE')
    return parser.parse_args()


def main():
    options, args = configure()

    if len(args) != 1:
        raise RuntimeError('sql file needed')

    if args[0] == '-':
        sql_file = sys.stdin
    else:
        sql_file = open(args[0], 'r')

    sql = sql_file.read()
    sql_file.close()

    config_parser = ConfigParser.ConfigParser()
    config_parser.read([options.config_file_name])
    hostname = config_parser.get('database', 'hostname')
    username = config_parser.get('database', 'username')
    pool_size = int(config_parser.get(
        'executor', 'pool_size', DEFAULT_POOLSIZE))
    renderer = RENDERERS[config_parser.get(
        'renderer', 'renderer', DEFAULT_RENDERER)]
    shard_name_pattern = config_parser.get('database', 'shard_name_pattern')

    is_shard_db = re.compile(shard_name_pattern).search

    print("Host: %s" % hostname)
    print("Username: %s" % username)
    print("Renderer: %s" % renderer.__name__)
    print("Executor Pool Size: %s" % pool_size)
    print("SQL to be executed for each database:\n\n%s" % highlight(sql))

    password = getpass.getpass()
    shard_databases = get_shard_databases(
        hostname, username, password, is_shard_db)

    if not shard_databases:
        raise RuntimeError(
            'Cannot get shard databases given the pattern: %s' % shard_name_pattern)

    pool = Pool(pool_size)

    collected = reduce(
        aggregate,
        pool.map(
            collect,
            [(sql, hostname, username, password, live)
             for live in shard_databases]),
        {}
    )
    print(renderer(collected))