# -*- coding: utf-8 -*-
"""
    Copyright (C) 2017 Sebastian Golasch (plugin.video.netflix)
    Copyright (C) 2018 Caphm (original implementation module)
    Copyright (C) 2020 Stefano Gottardo
    Kodi library integration

    SPDX-License-Identifier: MIT
    See LICENSES/MIT.md for more information.
"""
from __future__ import absolute_import, division, unicode_literals

from future.utils import iteritems

from datetime import datetime

import resources.lib.api.api_requests as api
import resources.lib.common as common
import resources.lib.kodi.nfo as nfo
import resources.lib.kodi.ui as ui
from resources.lib.globals import g
from resources.lib.kodi.library_tasks import LibraryTasks
from resources.lib.kodi.library_utils import (request_upd_kodi_library, get_library_path,
                                              FOLDER_NAME_MOVIES, FOLDER_NAME_SHOWS,
                                              is_auto_update_library_running, request_upd_kodi_library_decorator)
from resources.lib.navigation.directory_utils import delay_anti_ban

try:  # Python 2
    unicode
except NameError:  # Python 3
    unicode = str  # pylint: disable=redefined-builtin

# Reasons that led to the creation of a class for the library operations:
# - Time-consuming update functionality like "full sync of kodi library", "auto update", "export" (large tv show)
#    from context menu or settings, can not be performed within of the service side or will cause IPC timeouts,
#    and could block IPC access for other actions.
# - The scheduled update operations for the library require direct access to nfsession functions,
#    otherwise if you use the IPC callback to access to nfsession will cause the continuous display
#    of the loading screens while using Kodi, then to avoid the loading screen on update
#    is needed run the whole code within the service side.
# - Simple operations as "remove" can be executed directly without use of nfsession/IPC and speed up the operations.
# A class allows you to choice to retrieve the data from netflix API through IPC or directly from nfsession.


def get_library_cls():
    """
    Get the library class to do library operations
    FUNCTION TO BE USED ONLY ON ADD-ON CLIENT INSTANCES
    """
    # This build a instance of library class by assigning access to external functions through IPC
    return Library(api.get_metadata, api.get_mylist_videoids_profile_switch)


class Library(LibraryTasks):
    """Kodi library integration"""

    def __init__(self, func_get_metadata, func_get_mylist_videoids_profile_switch):
        super(Library, self).__init__()
        # External functions
        self.ext_func_get_metadata = func_get_metadata
        self.ext_func_get_mylist_videoids_profile_switch = func_get_mylist_videoids_profile_switch

    @request_upd_kodi_library_decorator
    def export_to_library(self, videoid, is_silent_mode=False):
        """
        Export an item to the Kodi library
        :param videoid: the videoid
        :param is_silent_mode: if True no GUI feedback except for NFO export (based on user settings)
        """
        nfo_settings = nfo.NFOSettings()
        nfo_settings.show_export_dialog(videoid.mediatype)
        self.execute_library_tasks(videoid,
                                   [self.export_item],
                                   title=common.get_local_string(30018),
                                   nfo_settings=nfo_settings,
                                   is_silent_mode=is_silent_mode)

    @request_upd_kodi_library_decorator
    def export_to_library_new_episodes(self, videoid, is_silent_mode=False):
        """
        Export new episodes for a tv show by it's videoid
        :param videoid: The videoid of the tv show to process
        :param is_silent_mode: if True no GUI feedback except for NFO export (based on user settings)
        :param nfo_settings: the nfo settings
        """
        if videoid.mediatype != common.VideoId.SHOW:
            common.debug('{} is not a tv show, no new episodes will be exported', videoid)
            return
        nfo_settings = nfo.NFOSettings()
        nfo_settings.show_export_dialog(videoid.mediatype)
        common.debug('Exporting new episodes for {}', videoid)
        self.execute_library_tasks(videoid,
                                   [self.export_new_item],
                                   title=common.get_local_string(30198),
                                   nfo_settings=nfo_settings,
                                   is_silent_mode=is_silent_mode)

    @request_upd_kodi_library_decorator
    def update_library(self, videoid, is_silent_mode=False):
        """
        Update items in the Kodi library
        :param videoid: the videoid
        :param is_silent_mode: if True no GUI feedback except for NFO export (based on user settings)
        """
        nfo_settings = nfo.NFOSettings()
        nfo_settings.show_export_dialog(videoid.mediatype)
        self.execute_library_tasks(videoid,
                                   [self.remove_item, self.export_item],
                                   title=common.get_local_string(30061),
                                   nfo_settings=nfo_settings,
                                   is_silent_mode=is_silent_mode)

    def remove_from_library(self, videoid, is_silent_mode=False):
        """
        Remove an item from the Kodi library
        :param videoid: the videoid
        :param is_silent_mode: if True no GUI feedback except for NFO export (based on user settings)
        """
        self.execute_library_tasks(videoid,
                                   [self.remove_item],
                                   title=common.get_local_string(30030),
                                   is_silent_mode=is_silent_mode)

    @request_upd_kodi_library_decorator
    def sync_library_with_mylist(self):
        """
        Perform a full sync of Kodi library with Netflix "My List",
        by deleting everything that was previously exported
        """
        common.info('Performing full sync of Netflix "My List" with the Kodi library')
        # Clear all the library
        self.clear_library()
        # Get NFO settings
        nfo_settings = nfo.NFOSettings()
        nfo_settings.show_export_dialog()
        # Start the sync
        # pylint: disable=not-callable
        mylist_video_id_list, mylist_video_id_list_type = self.ext_func_get_mylist_videoids_profile_switch()
        for index, video_id in enumerate(mylist_video_id_list):
            videoid = common.VideoId(
                **{('movieid' if (mylist_video_id_list_type[index] == 'movie') else 'tvshowid'): video_id})
            self.execute_library_tasks(videoid,
                                       [self.export_item],
                                       title=common.get_local_string(30018),
                                       nfo_settings=nfo_settings)
            if self.monitor.waitForAbort():
                break
            delay_anti_ban()

    @common.time_execution(immediate=True)
    def clear_library(self, is_silent_mode=False):
        """
        Delete all exported items to Kodi library, clean the add-on database, clean the folders
        :param is_silent_mode: if True no GUI feedback
        """
        common.info('Start deleting exported items to Kodi library')
        for videoid_value in g.SHARED_DB.get_movies_id_list():
            videoid = common.VideoId.from_path([common.VideoId.MOVIE, videoid_value])
            self.execute_library_tasks(videoid,
                                       [self.remove_item],
                                       title=common.get_local_string(30030),
                                       is_silent_mode=is_silent_mode)
        for videoid_value in g.SHARED_DB.get_tvshows_id_list():
            videoid = common.VideoId.from_path([common.VideoId.SHOW, videoid_value])
            self.execute_library_tasks(videoid,
                                       [self.remove_item],
                                       title=common.get_local_string(30030),
                                       is_silent_mode=is_silent_mode)
        # If for some reason such as improper use of the add-on, unexpected error or other
        # has caused inconsistencies with the contents of the database or stored files,
        # make sure that everything is removed
        g.SHARED_DB.purge_library()
        for folder_name in [FOLDER_NAME_MOVIES, FOLDER_NAME_SHOWS]:
            section_root_dir = common.join_folders_paths(get_library_path(), folder_name)
            common.delete_folder_contents(section_root_dir, delete_subfolders=True)

    @request_upd_kodi_library_decorator
    def auto_update_library(self, is_sync_with_mylist_enabled, is_silent_mode):
        """
        Perform an auto update of the exported items in to Kodi library.
        - The main purpose is check if there are new seasons/episodes.
        - In the case "Sync Kodi library with my list" feature is enabled, will be also synchronized with My List.
        :param is_sync_with_mylist_enabled: True to enable sync with My List
        :param is_silent_mode: if True no GUI feedback
        """
        if is_auto_update_library_running():
            return
        common.info('Starting auto update of Kodi library (sync with My List is {})',
                    'ENABLED' if is_sync_with_mylist_enabled else 'DISABLED')
        g.SHARED_DB.set_value('library_auto_update_is_running', True)
        g.SHARED_DB.set_value('library_auto_update_start_time', datetime.now())
        try:
            # Get the full list of the exported tvshows/movies as id (VideoId.value)
            exp_tvshows_videoids_values = g.SHARED_DB.get_tvshows_id_list()
            exp_movies_videoids_values = g.SHARED_DB.get_movies_id_list()

            # Get the exported tvshows (to be updated) as dict: key=videoid value=type of task
            videoids_tasks = {
                common.VideoId.from_path([common.VideoId.SHOW, videoid_value]): self.export_new_item
                for videoid_value in g.SHARED_DB.get_tvshows_id_list(common.VidLibProp['exclude_update'], False)
            }

            if is_sync_with_mylist_enabled:
                # Get My List videoids of the chosen profile
                # pylint: disable=not-callable
                mylist_video_id_list, mylist_video_id_list_type = self.ext_func_get_mylist_videoids_profile_switch()

                # Check if tv shows have been removed from the My List
                for videoid_value in exp_tvshows_videoids_values:
                    if unicode(videoid_value) in mylist_video_id_list:
                        continue
                    # The tv show no more exist in My List so remove it from library
                    videoid = common.VideoId.from_path([common.VideoId.SHOW, videoid_value])
                    videoids_tasks.update({videoid: self.remove_item})

                # Check if movies have been removed from the My List
                for videoid_value in exp_movies_videoids_values:
                    if unicode(videoid_value) in mylist_video_id_list:
                        continue
                    # The movie no more exist in My List so remove it from library
                    videoid = common.VideoId.from_path([common.VideoId.MOVIE, videoid_value])
                    videoids_tasks.update({videoid: self.remove_item})

                # Add to library the missing tv shows / movies of My List
                for index, videoid_value in enumerate(mylist_video_id_list):
                    if (int(videoid_value) not in exp_tvshows_videoids_values and
                            int(videoid_value) not in exp_movies_videoids_values):
                        is_movie = mylist_video_id_list_type[index] == 'movie'
                        videoid = common.VideoId(**{('movieid' if is_movie else 'tvshowid'): videoid_value})
                        videoids_tasks.update({videoid: self.export_new_item if is_movie else self.export_item})

            # Start the update operations
            ret = self._update_library(videoids_tasks, exp_tvshows_videoids_values, is_silent_mode)
            g.SHARED_DB.set_value('library_auto_update_is_running', False)
            if not ret:
                common.warn('Auto update of the Kodi library interrupted by Kodi')
                return
            common.info('Auto update of the Kodi library completed')
            if not g.ADDON.getSettingBool('lib_auto_upd_disable_notification'):
                ui.show_notification(common.get_local_string(30220), time=5000)
            request_upd_kodi_library()
        except Exception:  # pylint: disable=broad-except
            import traceback
            common.error('An error has occurred in the library auto update')
            common.error(g.py2_decode(traceback.format_exc(), 'latin-1'))
            g.SHARED_DB.set_value('library_auto_update_is_running', False)

    def _update_library(self, videoids_tasks, exp_tvshows_videoids_values, is_silent_mode):
        # Get the exported tvshows, but to be excluded from the updates
        excluded_videoids_values = g.SHARED_DB.get_tvshows_id_list(common.VidLibProp['exclude_update'], True)
        # Start the update operations
        for videoid, task_handler in iteritems(videoids_tasks):
            # Check if current videoid is excluded from updates
            if int(videoid.value) in excluded_videoids_values:
                continue
            # Get the NFO settings for the current videoid
            if int(videoid.value) in exp_tvshows_videoids_values:
                # It is possible that the user has chosen not to export NFO files for a tv show
                nfo_export = g.SHARED_DB.get_tvshow_property(videoid.value,
                                                             common.VidLibProp['nfo_export'], False)
                nfo_settings = nfo.NFOSettings(nfo_export)
            else:
                nfo_settings = nfo.NFOSettings()
            # Execute the task
            self.execute_library_tasks(videoid,
                                       [task_handler],
                                       nfo_settings=nfo_settings,
                                       is_silent_mode=is_silent_mode)
            if self.monitor.waitForAbort():
                return False
            delay_anti_ban()
        return True

    def import_library(self, is_old_format):
        """
        Imports an already existing library into the add-on library database,
        allows you to recover an existing library, avoiding to recreate it from scratch.
        :param is_old_format: if True, imports library items with old format version (add-on version 13.x)
        """
        nfo_settings = nfo.NFOSettings()
        if is_old_format:
            for videoid in self.imports_videoids_from_existing_old_library():
                self.execute_library_tasks(videoid,
                                           [self.export_item],
                                           nfo_settings=nfo_settings,
                                           title=common.get_local_string(30018))
            if self.monitor.waitForAbort():
                return False
            # Here delay_anti_ban is not needed metadata are already cached
        else:
            raise NotImplementedError
