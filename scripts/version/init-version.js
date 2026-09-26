// commit-and-tag-version updater for the package __version__ surface.

const pattern = /^(__version__ = ")[^"]*(")/m;

module.exports.readVersion = function (contents) {
  const match = contents.match(pattern);
  if (!match) {
    throw new Error("__version__ not found in __init__.py");
  }
  return match[0].match(/"([^"]*)"/)[1];
};

module.exports.writeVersion = function (contents, version) {
  if (!pattern.test(contents)) {
    throw new Error("__version__ not found in __init__.py");
  }
  return contents.replace(pattern, `$1${version}$2`);
};
