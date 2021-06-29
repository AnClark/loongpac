#!/usr/bin/python

import re, subprocess, os

CACHE_PKGBUILD_FILE_PATH = "/home/anclark/.cache/loongpac/pkgbuild/"


def init_cache_dir():
    if not os.path.exists(CACHE_PKGBUILD_FILE_PATH):
        os.makedirs(CACHE_PKGBUILD_FILE_PATH)


def bash_process_string(s):
    s = s.replace(">", "\\>")
    bash_cmd = 'bash -c \"echo {src_str}\"'.format(src_str = s)
    return os.popen(bash_cmd).read().strip()


def trim_pkgbuild(pkgbuild_content):
    # Trim comments
    found_list = re.findall(r"\#.*", pkgbuild_content, re.M)
    for i in found_list:
        pkgbuild_content = pkgbuild_content.replace(i, "\n")

    return pkgbuild_content


def get_raw_pkgbuild(pkg_name):
    asp_session = subprocess.Popen(['asp', 'show', str(pkg_name)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # Check for multi-in-one package
    out = asp_session.stdout.read()
    actual_package = ""
    if(re.match(r".*is part of package.*", out, re.M)):
        actual_package = re.findall(r"is part of package\s*([\w\-]+)", out, re.M)[0]

    return {"out": trim_pkgbuild(out), "err": asp_session.stderr.read(), "actual_package": actual_package}


def get_raw_pkgbuild_cached(pkg_name):
    cache_file = CACHE_PKGBUILD_FILE_PATH + "PKGBUILD_%s" % pkg_name
    out = ""
    err = ""

    init_cache_dir()

    if os.path.exists(cache_file):
        f = open(cache_file, "r")
        out = f.read()
        f.close()
    else:
        asp_session = subprocess.Popen(['asp', 'show', str(pkg_name)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out = asp_session.stdout.read()
        err = asp_session.stderr.read()
        
        f = open(cache_file, "w")
        f.write(out)
        f.close()

    # Check for multi-in-one package
    actual_package = ""
    if(re.match(r".*is part of package.*", out, re.M)):
        actual_package = re.findall(r"is part of package\s*([\w\-]+)", out, re.M)[0]

    return {"out": out, "err": err, "actual_package": actual_package}


def parse_depends(pkgbuild_content):
    # Regex to parse "depend=()" array's inner string
    raw_depends_list_parser = re.compile(r"^depends=[\(]([^\(\)]*)[\)]", re.M)

    # Bypass PKGBUILDs without dependencies
    if not raw_depends_list_parser.search(pkgbuild_content):
        return []

    # Extract array's inner string, then get package names
    raw_depends_list = raw_depends_list_parser.findall(pkgbuild_content)[0]
    package_list = re.findall(r"[\w\-\.<>=\_\+\-]+", raw_depends_list, re.M)

    # Filter out package name, including: version specification, library files (*.so).
    # Processing them is not Loongpac's job.
    package_list_filtered = []
    for pkg_name in package_list:
        if re.match(r"(.*)[<>=]+(.*)", pkg_name):
            real_pkg_name = re.findall(r"([^<>=]+)[<>=]+.*", pkg_name)[0]
            print("%s includes version specification. Get package name: %s" % (pkg_name, real_pkg_name))
            package_list_filtered.append(real_pkg_name)
        else:
            package_list_filtered.append(pkg_name)

    return package_list_filtered


def parse_provides(pkgbuild_content):
    # Regex to parse "depend=()" array's inner string
    raw_provides_list_parser = re.compile(r"^provides=[\(]([^\(\)]*)[\)]", re.M)

    # Bypass PKGBUILDs without dependencies
    if not raw_provides_list_parser.search(pkgbuild_content):
        return []

    # Extract array's inner string, then get package names
    raw_provides_list = raw_provides_list_parser.findall(pkgbuild_content)[0]
    package_list = re.findall(r"[^\s\'\"]+", raw_provides_list, re.M)

    # Parse or filter out package name, including: version specification, Case  modification
    # Processing them is not Loongpac's job.
    package_list_filtered = []
    for pkg_name in package_list:
        # Preprocess string with bash
        pkg_name_prep = bash_process_string(pkg_name).split(" ")
        for p in pkg_name_prep:
            if re.match(r"(.*)[<>=]+(.*)", p):
                real_pkg_name = re.findall(r"([^<>=]+)[<>=]+.*", p)[0]
                package_list_filtered.append(real_pkg_name)
            else:
                package_list_filtered.append(p)

    return package_list_filtered


def populate_dependency_list(pkg_name):
    dependency_table = {}
    provides_table = {}

    def worker(pkg_name):
        # Get PKGBUILD
        pkgbuild = ""
        raw_pkgbuild = get_raw_pkgbuild_cached(pkg_name)
        while raw_pkgbuild["actual_package"] != "":         # Process package alias ("PKG1 is part of PKG2")
            actual_pkg = raw_pkgbuild["actual_package"]
            print("{alias} is part of {actual}, will build {actual}".format(alias=pkg_name, actual=actual_pkg))

            # Mark out package alias so that it won't be build twice
            dependency_table[pkg_name] = ["__ALIAS_OF__", actual_pkg]       

            # Then, process the real package
            pkg_name = actual_pkg
            raw_pkgbuild = get_raw_pkgbuild_cached(actual_pkg)

        pkgbuild = raw_pkgbuild["out"]

        depends = parse_depends(pkgbuild)
        dependency_table[pkg_name] = depends
        print("%s depends:" % pkg_name, depends)

        provides = parse_provides(pkgbuild)
        provides_table[pkg_name] = provides

        for d in depends:
            if d not in dependency_table.keys():
                worker(d)

    def process_provides():
        for provider in provides_table.keys():
            for item in provides_table[provider]:
                if (item not in dependency_table.keys()) or len(dependency_table[item]) <= 0:
                    dependency_table[item] = ["__PROVIDED_BY__", provider]
                else:
                    dependency_table[item].append(provider)

    worker(pkg_name)

    process_provides()

    return dependency_table


def generate_makefile(dependency_table):
    MAKEFILE_TARGET_TEMPLATE = """
{name}: {depends}
\t@echo "Will build target: {name}"
"""
    # Template to process packages with alias(es) ("PKG1 is part of PKG2")
    MAKEFILE_TARGET_TEMPLATE_ALIAS = """
{alias}: {orig_package}
\t@echo "{alias} is part of {orig_package}"
"""

    MAKEFILE_TARGET_TEMPLATE_PROVIDED_BY = """
{alias}: {providers}
\t@echo "{alias} is provided by: {providers}"
"""

    makefile_content = ""

    def list_to_string(list):
        out = ""
        for i in list:
            out += i + " "
        return out

    for k in dependency_table.keys():
        if len(dependency_table[k]) > 0 and dependency_table[k][0] == "__ALIAS_OF__":
            makefile_content += MAKEFILE_TARGET_TEMPLATE_ALIAS.format(alias=k, orig_package=dependency_table[k][1])
        elif len(dependency_table[k]) > 0 and dependency_table[k][0] == "__PROVIDED_BY__":
            makefile_content += MAKEFILE_TARGET_TEMPLATE_PROVIDED_BY.format(alias=k, providers=list_to_string(dependency_table[k][1:]))
        else:
            makefile_content += MAKEFILE_TARGET_TEMPLATE.format(name=k, depends=list_to_string(dependency_table[k]))

    return makefile_content


