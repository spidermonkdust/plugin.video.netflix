# -*- coding: utf-8 -*-
"""
    Copyright (C) 2017 Sebastian Golasch (plugin.video.netflix)
    Copyright (C) 2018 Caphm (original implementation module)
    Copyright (C) 2019 Stefano Gottardo - @CastagnaIT
    Kodi library integration: task management

    SPDX-License-Identifier: MIT
    See LICENSES/MIT.md for more information.
"""
from __future__ import absolute_import, division, unicode_literals

import os
import re

import xbmcgui

import resources.lib.common as common
import resources.lib.kodi.nfo as nfo
from resources.lib.api.exceptions import MetadataNotAvailable
from resources.lib.database.db_utils import VidLibProp
from resources.lib.globals import g
from resources.lib.kodi.ui import show_library_task_errors
from resources.lib.kodi.library_items import LibraryItems
from resources.lib.kodi.library_utils import (get_episode_title_from_path, get_library_path,
                                              request_upd_kodi_library, ILLEGAL_CHARACTERS,
                                              FOLDER_NAME_MOVIES, FOLDER_NAME_SHOWS)


class LibraryTasks(LibraryItems):

    def execute_library_tasks(self, videoid, task_handlers, title=None, nfo_settings=None, is_silent_mode=False):
        """
        Execute library tasks for a videoid and show errors in foreground
        :param videoid: the videoid
        :param task_handlers: list of task handler for the operations to do
        :param title: title of the video (will be shown in the progress dialog window)
        :param nfo_settings: the NFOSettings object containing the user's NFO settings
        :param is_silent_mode: if True do not show any GUI feedback
        """
        if is_silent_mode:
            for task_handler in task_handlers:
                for task in self.compile_tasks(videoid, task_handler, nfo_settings):
                    try:
                        task_handler(task, get_library_path())
                    except Exception:  # pylint: disable=broad-except
                        import traceback
                        common.error(g.py2_decode(traceback.format_exc(), 'latin-1'))
                        common.error('{} of {} failed', task_handler.__name__, task['title'])
        else:
            for task_handler in task_handlers:
                self.execute_tasks(title=title,
                                   tasks=self.compile_tasks(videoid, task_handler, nfo_settings),
                                   task_handler=task_handler,
                                   notify_errors=True,
                                   library_home=get_library_path())
        request_upd_kodi_library(g.KODI_VERSION.is_major_ver('18') and task_handlers != [self.remove_item])

    def execute_tasks(self, title, tasks, task_handler, **kwargs):
        """
        Run all tasks through task_handler and display a progress dialog in the GUI. Additional kwargs will be
        passed into task_handler on each invocation.
        Returns a list of errors that occurred during execution of tasks.
        """
        errors = []
        notify_errors = kwargs.pop('notify_errors', False)
        progress = xbmcgui.DialogProgress()
        progress.create(title)
        for task_num, task in enumerate(tasks):
            task_title = task.get('title', 'Unknown Task')
            progress.update(int(task_num * 100 / len(tasks)), task_title)
            if progress.iscanceled():
                break
            if not task:
                continue
            try:
                task_handler(task, **kwargs)
            except Exception as exc:  # pylint: disable=broad-except
                import traceback
                common.error(g.py2_decode(traceback.format_exc(), 'latin-1'))
                errors.append({
                    'task_title': task_title,
                    'error': '{}: {}'.format(type(exc).__name__, exc)})
        show_library_task_errors(notify_errors, errors)
        return errors

    @common.time_execution(immediate=True)
    def compile_tasks(self, videoid, task_handler, nfo_settings=None):
        """Compile a list of tasks for items based on the videoid"""
        common.debug('Compiling library tasks for task handler "{}" and videoid "{}"', task_handler.__name__, videoid)
        tasks = None
        try:
            if task_handler == self.export_item:
                metadata = self.ext_func_get_metadata(videoid)  # pylint: disable=not-callable
                if videoid.mediatype == common.VideoId.MOVIE:
                    tasks = self._create_export_movie_task(videoid, metadata[0], nfo_settings)
                elif videoid.mediatype in common.VideoId.TV_TYPES:
                    tasks = self._create_export_tv_tasks(videoid, metadata, nfo_settings)
                else:
                    raise ValueError('compile_tasks: cannot handle videoid "{}" for task handler "{}"'
                                     .format(videoid, task_handler.__name__))

            if task_handler == self.export_new_item:
                metadata = self.ext_func_get_metadata(videoid, True)  # pylint: disable=not-callable
                tasks = self._create_new_episodes_tasks(videoid, metadata, nfo_settings)

            if task_handler == self.remove_item:
                if videoid.mediatype == common.VideoId.MOVIE:
                    tasks = self._create_remove_movie_task(videoid)
                if videoid.mediatype == common.VideoId.SHOW:
                    tasks = self._compile_remove_tvshow_tasks(videoid)
                if videoid.mediatype == common.VideoId.SEASON:
                    tasks = self._compile_remove_season_tasks(videoid)
                if videoid.mediatype == common.VideoId.EPISODE:
                    tasks = self._create_remove_episode_task(videoid)
        except MetadataNotAvailable:
            common.warn('compile_tasks: unavailable metadata for videoid "{}" tasks compiling skipped',
                        task_handler, videoid)
            return [{}]
        if tasks is None:
            common.warn('compile_tasks: no tasks have been compiled for task handler "{}" and videoid "{}"',
                        task_handler.__name__, videoid)
        return tasks

    def _create_export_movie_task(self, videoid, movie, nfo_settings):
        """Create a task for a movie"""
        # Reset NFO export to false if we never want movies nfo
        filename = '{title} ({year})'.format(title=movie['title'], year=movie['year'])
        create_nfo_file = nfo_settings and nfo_settings.export_movie_enabled
        nfo_data = nfo.create_movie_nfo(movie) if create_nfo_file else None
        return [self._create_export_item_task(True, create_nfo_file,
                                              videoid=videoid, title=movie['title'],
                                              root_folder_name=FOLDER_NAME_MOVIES,
                                              folder_name=filename,
                                              filename=filename,
                                              nfo_data=nfo_data)]

    def _create_export_tv_tasks(self, videoid, metadata, nfo_settings):
        """Create tasks for a show, season or episode.
        If videoid represents a show or season, tasks will be generated for
        all contained seasons and episodes"""
        if videoid.mediatype == common.VideoId.SHOW:
            tasks = self._compile_export_show_tasks(videoid, metadata[0], nfo_settings)
        elif videoid.mediatype == common.VideoId.SEASON:
            tasks = self._compile_export_season_tasks(videoid,
                                                      metadata[0],
                                                      common.find(int(videoid.seasonid),
                                                                  'id',
                                                                  metadata[0]['seasons']),
                                                      nfo_settings)
        else:
            tasks = [self._create_export_episode_task(videoid, *metadata, nfo_settings=nfo_settings)]

        if nfo_settings and nfo_settings.export_full_tvshow:
            # Create tvshow.nfo file
            # In episode metadata, show data is at 3rd position,
            # while it's at first position in show metadata.
            # Best is to enumerate values to find the correct key position
            key_index = -1
            for i, item in enumerate(metadata):
                if item and item.get('type', None) == 'show':
                    key_index = i
            if key_index > -1:
                tasks.append(self._create_export_item_task(False, True,
                                                           videoid=videoid, title='tvshow.nfo',
                                                           root_folder_name=FOLDER_NAME_SHOWS,
                                                           folder_name=metadata[key_index]['title'],
                                                           filename='tvshow',
                                                           nfo_data=nfo.create_show_nfo(metadata[key_index])))
        return tasks

    def _compile_export_show_tasks(self, videoid, show, nfo_settings):
        """Compile a list of task items for all episodes of all seasons of a tvshow"""
        tasks = []
        for season in show['seasons']:
            tasks += self._compile_export_season_tasks(videoid.derive_season(season['id']), show, season, nfo_settings)
        return tasks

    def _compile_export_season_tasks(self, videoid, show, season, nfo_settings):
        """Compile a list of task items for all episodes in a season"""
        return [self._create_export_episode_task(videoid.derive_episode(episode['id']),
                                                 episode, season, show, nfo_settings)
                for episode in season['episodes']]

    def _create_export_episode_task(self, videoid, episode, season, show, nfo_settings):
        """Export a single episode to the library"""
        filename = 'S{:02d}E{:02d}'.format(season['seq'], episode['seq'])
        title = ' - '.join((show['title'], filename, episode['title']))
        create_nfo_file = nfo_settings and nfo_settings.export_tvshow_enabled
        nfo_data = nfo.create_episode_nfo(episode, season, show) if create_nfo_file else None
        return self._create_export_item_task(True, create_nfo_file,
                                             videoid=videoid, title=title,
                                             root_folder_name=FOLDER_NAME_SHOWS,
                                             folder_name=show['title'],
                                             filename=filename,
                                             nfo_data=nfo_data)

    def _create_export_item_task(self, create_strm_file, create_nfo_file, **kwargs):
        """Create a single task item"""
        return {
            'create_strm_file': create_strm_file,  # True/False
            'create_nfo_file': create_nfo_file,  # True/False
            'videoid': kwargs['videoid'],
            'title': kwargs['title'],  # Only for debug purpose
            'root_folder_name': kwargs['root_folder_name'],
            'folder_name': re.sub(ILLEGAL_CHARACTERS, '', kwargs['folder_name']),
            'filename': re.sub(ILLEGAL_CHARACTERS, '', kwargs['filename']),
            'nfo_data': kwargs['nfo_data']
        }

    def _create_new_episodes_tasks(self, videoid, metadata, nfo_settings=None):
        tasks = []
        if metadata and 'seasons' in metadata[0]:
            for season in metadata[0]['seasons']:
                if not nfo_settings:
                    nfo_export = g.SHARED_DB.get_tvshow_property(videoid.value, VidLibProp['nfo_export'], False)
                    nfo_settings = nfo.NFOSettings(nfo_export)
                # Check and add missing seasons and episodes
                self._add_missing_items(tasks, season, videoid, metadata, nfo_settings)
        return tasks

    def _add_missing_items(self, tasks, season, videoid, metadata, nfo_settings):
        if g.SHARED_DB.season_id_exists(videoid.value, season['id']):
            # The season exists, try to find any missing episode
            for episode in season['episodes']:
                if not g.SHARED_DB.episode_id_exists(videoid.value, season['id'], episode['id']):
                    tasks.append(self._create_export_episode_task(
                        videoid=videoid.derive_season(season['id']).derive_episode(episode['id']),
                        episode=episode,
                        season=season,
                        show=metadata[0],
                        nfo_settings=nfo_settings
                    ))
                    common.debug('Auto exporting episode {}', episode['id'])
        else:
            # The season does not exist, build task for the season
            tasks += self._compile_export_season_tasks(
                videoid=videoid.derive_season(season['id']),
                show=metadata[0],
                season=season,
                nfo_settings=nfo_settings
            )
            common.debug('Auto exporting season {}', season['id'])

    def _create_remove_movie_task(self, videoid):
        file_path = g.SHARED_DB.get_movie_filepath(videoid.value)
        title = os.path.splitext(os.path.basename(file_path))[0]
        return [self._create_remove_item_task(title, file_path, videoid)]

    def _compile_remove_tvshow_tasks(self, videoid):
        row_results = g.SHARED_DB.get_all_episodes_ids_and_filepath_from_tvshow(videoid.value)
        return self._create_remove_tv_tasks(row_results)

    def _compile_remove_season_tasks(self, videoid):
        row_results = g.SHARED_DB.get_all_episodes_ids_and_filepath_from_season(
            videoid.tvshowid, videoid.seasonid)
        return self._create_remove_tv_tasks(row_results)

    def _create_remove_episode_task(self, videoid):
        file_path = g.SHARED_DB.get_episode_filepath(
            videoid.tvshowid, videoid.seasonid, videoid.episodeid)
        return [self._create_remove_item_task(
            get_episode_title_from_path(file_path),
            file_path, videoid)]

    def _create_remove_tv_tasks(self, row_results):
        return [self._create_remove_item_task(get_episode_title_from_path(row['FilePath']),
                                              row['FilePath'],
                                              common.VideoId.from_dict(
                                                  {'mediatype': common.VideoId.SHOW,
                                                   'tvshowid': row['TvShowID'],
                                                   'seasonid': row['SeasonID'],
                                                   'episodeid': row['EpisodeID']}))
                for row in row_results]

    def _create_remove_item_task(self, title, file_path, videoid):
        """Create a single task item"""
        return {
            'title': title,
            'file_path': file_path,
            'videoid': videoid
        }
