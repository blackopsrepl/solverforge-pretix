// commit-and-tag-version updater for the pyproject.toml project version.
// Only the [project] version line is the release surface; other `version`
// occurrences (build requirements, tool targets) must not be touched.

const pattern = /^(\[project\][\s\S]*?^version = ")[^"]*(")/m;

module.exports.readVersion = function (contents) {
  const match = contents.match(pattern);
  if (!match) {
    throw new Error("project version not found in pyproject.toml");
  }
  return match[0].match(/version = "([^"]*)"/)[1];
};

module.exports.writeVersion = function (contents, version) {
  if (!pattern.test(contents)) {
    throw new Error("project version not found in pyproject.toml");
  }
  return contents.replace(pattern, `$1${version}$2`);
};
