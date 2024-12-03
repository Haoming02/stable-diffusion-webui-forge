import threading
import diskcache
import json
import tqdm
import os

from modules.paths import data_path, script_path

cache_lock = threading.Lock()
cache_filename = os.environ.get("SD_WEBUI_CACHE_FILE", os.path.join(data_path, "cache.json"))
cache_dir = os.environ.get("SD_WEBUI_CACHE_DIR", os.path.join(data_path, "cache"))
caches = {}


dump_cache = lambda: None
"""does nothing since diskcache"""


def convert_old_cached_data():
    try:
        with open(cache_filename, "r", encoding="utf8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return
    except Exception:
        os.replace(cache_filename, os.path.join(script_path, "tmp", "cache.json"))
        print("failed to read cache.json; file has been moved to tmp/cache.json")
        return

    total_count = sum(len(keyvalues) for keyvalues in data.values())

    with tqdm.tqdm(total=total_count, desc="converting cache") as progress:
        for subsection, keyvalues in data.items():
            cache_obj = caches.get(subsection)
            if cache_obj is None:
                cache_obj = diskcache.Cache(os.path.join(cache_dir, subsection))
                caches[subsection] = cache_obj

            for key, value in keyvalues.items():
                cache_obj[key] = value
                progress.update(1)


def cache(subsection):
    """
    Retrieves or initializes a cache for a specific subsection.

    Parameters:
        subsection (str): The subsection identifier for the cache.

    Returns:
        diskcache.Cache: The cache data for the specified subsection.
    """

    cache_obj = caches.get(subsection)
    if not cache_obj:
        with cache_lock:
            if not os.path.exists(cache_dir) and os.path.isfile(cache_filename):
                convert_old_cached_data()

            cache_obj = caches.get(subsection)
            if not cache_obj:
                cache_obj = diskcache.Cache(os.path.join(cache_dir, subsection))
                caches[subsection] = cache_obj

    return cache_obj


def cached_data_for_file(subsection, title, filename, func):
    """
    Retrieves or generates data for a specific file, using a caching mechanism.

    Parameters:
        subsection (str): The subsection of the cache to use.
        title (str): The title of the data entry in the subsection of the cache.
        filename (str): The path to the file to be checked for modifications.
        func (callable): A function that generates the data if it is not available in the cache.

    Returns:
        dict or None: The cached or generated data, or None if data generation fails.

    The function implements a caching mechanism for data stored in files.
    It checks if the data associated with the given `title` is present in the cache and compares the
    modification time of the file with the cached modification time. If the file has been modified,
    the cache is considered invalid and the data is regenerated using the provided `func`.
    Otherwise, the cached data is returned.

    If the data generation fails, None is returned to indicate the failure. Otherwise, the generated
    or cached data is returned as a dictionary.
    """

    existing_cache = cache(subsection)
    ondisk_mtime = os.path.getmtime(filename)

    entry = existing_cache.get(title)
    if entry:
        cached_mtime = entry.get("mtime", 0)
        if ondisk_mtime > cached_mtime:
            entry = None

    if not entry or "value" not in entry:
        value = func()
        if value is None:
            return None

        entry = {"mtime": ondisk_mtime, "value": value}
        existing_cache[title] = entry

        dump_cache()

    return entry["value"]


def prune_unused_hash():
    import glob

    from modules.paths_internal import extensions_dir

    existing_cache = cache("extensions-git")
    total_count = len(existing_cache)
    with tqdm.tqdm(total=total_count, desc="pruning extensions") as progress:
        for name in existing_cache:
            if not os.path.isdir(os.path.join(extensions_dir, name)):
                existing_cache.pop(name)
            progress.update(1)

    def file_exists(parent_dir, filename):
        matches = glob.glob(os.path.join(parent_dir, "**", f"{filename}*"), recursive=True)
        return len(matches) > 0

    from modules.paths_internal import models_path
    from modules.shared import cmd_opts

    for db in ("hashes", "hashes-addnet", "safetensors-metadata"):
        existing_cache = cache(db)
        total_count = len(existing_cache)
        with tqdm.tqdm(total=total_count, desc=f"pruning {db}") as progress:
            for name in existing_cache:
                if not "/" in name:
                    progress.update(1)
                    continue

                category, filename = name.split("/", 1)
                if category.lower() == "lora":
                    exists = file_exists(os.path.join(models_path, "Lora"), filename)
                elif category.lower() == "checkpoint":
                    exists = file_exists(os.path.join(models_path, "Stable-diffusion"), filename)
                elif category.lower() == "textual_inversion":
                    exists = file_exists(cmd_opts.embeddings_dir, filename)
                else:
                    progress.update(1)
                    continue

                if not exists:
                    del existing_cache[name]
                progress.update(1)

    print("Finish pruning hash")
