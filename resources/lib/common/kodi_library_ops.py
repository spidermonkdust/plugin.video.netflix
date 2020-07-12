# -*- coding: utf-8 -*-
"""
    Copyright (C) 2017 Sebastian Golasch (plugin.video.netflix)
    Copyright (C) 2018 Caphm (original implementation module)
    Helper functions for Kodi library operations

    SPDX-License-Identifier: MIT
    See LICENSES/MIT.md for more information.
"""
from __future__ import absolute_import, division, unicode_literals

import os

import xbmc

from resources.lib.globals import g
from .kodi_ops import json_rpc, get_local_string
from .logging import debug, warn
from .videoid import VideoId

try:  # Kodi >= 19
    from xbmcvfs import makeLegalFilename  # pylint: disable=ungrouped-imports
except ImportError:  # Kodi 18
    from xbmc import makeLegalFilename  # pylint: disable=ungrouped-imports


LIBRARY_PROPS = {
    'episode': ['title', 'plot', 'writer', 'playcount', 'director', 'season',
                'episode', 'originaltitle', 'showtitle', 'lastplayed', 'file',
                'resume', 'dateadded', 'art', 'userrating', 'firstaired', 'runtime'],
    'movie': ['title', 'genre', 'year', 'director', 'trailer',
              'tagline', 'plot', 'plotoutline', 'originaltitle', 'lastplayed',
              'playcount', 'writer', 'studio', 'mpaa', 'country',
              'imdbnumber', 'runtime', 'set', 'showlink', 'premiered',
              'top250', 'file', 'sorttitle', 'resume', 'setid', 'dateadded',
              'tag', 'art', 'userrating']
}


class ItemNotFound(Exception):
    """The requested item could not be found in the Kodi library"""


def update_library_item_details(dbtype, dbid, details):
    """Update properties of an item in the Kodi library"""
    method = 'VideoLibrary.Set{}Details'.format(dbtype.capitalize())
    params = {'{}id'.format(dbtype): dbid}
    params.update(details)
    return json_rpc(method, params)


def get_library_items(dbtype, video_filter=None):
    """Return a list of all items in the Kodi library that are of type dbtype (either movie or episode)"""
    method = 'VideoLibrary.Get{}s'.format(dbtype.capitalize())
    params = {'properties': ['file']}
    if video_filter:
        params.update({'filter': video_filter})
    return json_rpc(method, params)[dbtype + 's']


def get_library_item_details(dbtype, itemid):
    """Return details for an item from the Kodi library"""
    method = 'VideoLibrary.Get{}Details'.format(dbtype.capitalize())
    params = {
        dbtype + 'id': itemid,
        'properties': LIBRARY_PROPS[dbtype]}
    return json_rpc(method, params)[dbtype + 'details']


def scan_library(path=""):
    """Start a library scanning in a specified folder"""
    method = 'VideoLibrary.Scan'
    params = {'directory': path}
    return json_rpc(method, params)


def get_library_item_by_videoid(videoid):
    """Find an item in the Kodi library by its Netflix videoid and return Kodi DBID and mediatype"""
    try:
        file_path, media_type = _get_library_entry(videoid)
        return _get_library_item(media_type, file_path)
    except (KeyError, IndexError, ItemNotFound):
        raise ItemNotFound('The video with id {} is not present in the Kodi library'.format(videoid))


def _get_library_entry(videoid):
    if videoid.mediatype == VideoId.MOVIE:
        file_path = g.SHARED_DB.get_movie_filepath(videoid.value)
        media_type = videoid.mediatype
    elif videoid.mediatype == VideoId.EPISODE:
        file_path = g.SHARED_DB.get_episode_filepath(videoid.tvshowid,
                                                     videoid.seasonid,
                                                     videoid.episodeid)
        media_type = videoid.mediatype
    elif videoid.mediatype == VideoId.SHOW:
        file_path = g.SHARED_DB.get_random_episode_filepath_from_tvshow(videoid.value)
        media_type = VideoId.EPISODE
    elif videoid.mediatype == VideoId.SEASON:
        file_path = g.SHARED_DB.get_random_episode_filepath_from_season(videoid.tvshowid,
                                                                        videoid.seasonid)
        media_type = VideoId.EPISODE
    else:
        # Items of other mediatype are never in library
        raise ItemNotFound
    if file_path is None:
        raise ItemNotFound
    return file_path, media_type


def _get_library_item(mediatype, filename):
    # TODO: verificare valori variabili per capire cosa contengono e assegnare nomi corretti alle var--------------------
    # To ensure compatibility with previously exported items, make the filename legal
    fname = makeLegalFilename(filename)
    dir_path = os.path.dirname(g.py2_decode(xbmc.translatePath(fname)))
    shortname = os.path.basename(g.py2_decode(xbmc.translatePath(fname)))
    # We get the data from Kodi library using filters, this is much faster than loading all episodes in memory.
    if fname[:10] == 'special://':
        # If the path is special, search with real directory path and also special path
        special_path = os.path.dirname(g.py2_decode(fname))
        path_filter = {'or': [{'field': 'path', 'operator': 'startswith', 'value': dir_path},
                              {'field': 'path', 'operator': 'startswith', 'value': special_path}]}
    else:
        path_filter = {'field': 'path', 'operator': 'startswith', 'value': dir_path}
    # Now build the all request and call the json-rpc function through get_library_items
    library_item = get_library_items(
        mediatype,
        {'and': [path_filter, {'field': 'filename', 'operator': 'is', 'value': shortname}]}
    )[0]
    if not library_item:
        raise ItemNotFound
    return get_library_item_details(mediatype, library_item[mediatype + 'id'])


def remove_videoid_from_kodi_library(videoid):
    """Remove an item from the Kodi library (not related files)"""
    try:
        kodi_library_items = [get_library_item_by_videoid(videoid)]
        if videoid.mediatype in [VideoId.SHOW, VideoId.SEASON]:
            # Retrieve the all episodes in the export folder
            filters = {'and': [
                {'field': 'path', 'operator': 'startswith',
                 'value': os.path.dirname(kodi_library_items[0]['file'])},
                {'field': 'filename', 'operator': 'endswith', 'value': '.strm'}
            ]}
            if videoid.mediatype == VideoId.SEASON:
                # Add a season filter in case we just want to remove a season
                filters['and'].append({'field': 'season', 'operator': 'is',
                                       'value': str(kodi_library_items[0]['season'])})
            kodi_library_items = get_library_items(VideoId.EPISODE, filters)
        for item in kodi_library_items:
            rpc_params = {
                'movie': ['VideoLibrary.RemoveMovie', 'movieid'],
                # We should never remove an entire show
                # 'show': ['VideoLibrary.RemoveTVShow', 'tvshowid'],
                # Instead we delete all episodes listed in the JSON query above
                'show': ['VideoLibrary.RemoveEpisode', 'episodeid'],
                'season': ['VideoLibrary.RemoveEpisode', 'episodeid'],
                'episode': ['VideoLibrary.RemoveEpisode', 'episodeid']
            }[videoid.mediatype]
            debug(item)
            json_rpc(rpc_params[0], {rpc_params[1]: item[rpc_params[1]]})
    except ItemNotFound:
        warn('Cannot remove {} from Kodi library, item not present', videoid)
    except KeyError as exc:
        from resources.lib.kodi import ui
        ui.show_notification(get_local_string(30120), time=7500)
        warn('Cannot remove {} from Kodi library, Kodi does not support this (yet)', exc)
