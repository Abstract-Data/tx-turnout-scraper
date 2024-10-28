from __future__ import annotations
from typing import Optional
from pydantic.dataclasses import dataclass as pydantic_dataclass
import selenium.common.exceptions
from selenium import webdriver
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from time import sleep
from datetime import datetime
from pathlib import Path
import shutil
import zipfile
import glob
import tomli
from icecream import ic

from models import ReadElectionData

# TODO: Turn into a input() to ask what path to save to.
# TODO: Turn into a input() to ask if you want to save the path to a file.
# TODO: Rewrite so that the driver settings are created later in the script.


@pydantic_dataclass(config={'arbitrary_types_allowed': True})
class ScraperConfig:
    config: Optional[dict] = None
    user_options: Optional[dict] = None
    save_options: Optional[bool] = False

    def __post_init__(self):
        with open(Path(__file__).parent / "sos_fields.toml", 'rb') as f:
            self.config = tomli.load(f)
        with open(Path(__file__).parent / "usr_options.toml", 'rb') as f:
            self.user_options = tomli.load(f)

        self.ask_to_save_options()
        self.set_download_path()

    def ask_to_save_options(self):
        _save_response = input("Would you like to save your settings? (y/n): ")
        match _save_response.lower():
            case 'y':
                self.save_options = True
            case 'n':
                self.save_options = False
            case _:
                raise ValueError("Invalid response. Please enter 'y' or 'n'.")

    def set_download_path(self, _download_path: Path = None) -> Path:
        if not _download_path:
            DOWNLOAD_FOLDER = Path(self.user_options.get("DOWNLOAD_FOLDER"))
        else:
            DOWNLOAD_FOLDER = Path(_download_path)

        if DOWNLOAD_FOLDER is None:
            _path = input("Enter the path to save the files to: ")
            DOWNLOAD_FOLDER = Path(_path)
        else:
            change_path = input(f"Download path is currently {DOWNLOAD_FOLDER} Would you like to change the path? (y/n): ")
            if change_path.lower() == "y":
                _path = input("Enter the path to save the files to: ")
                DOWNLOAD_FOLDER = Path(_path)

        if self.save_options:
            self.user_options['DOWNLOAD_FOLDER'] = DOWNLOAD_FOLDER

        DOWNLOAD_FOLDER = DOWNLOAD_FOLDER.joinpath("tx_vote_rosters")

        if not DOWNLOAD_FOLDER.exists():
            DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
            ic("Download folder created: ", DOWNLOAD_FOLDER)
        return DOWNLOAD_FOLDER



@pydantic_dataclass(config={'arbitrary_types_allowed': True})
class CreateScraper:
    config: ScraperConfig = ScraperConfig()
    driver: Optional[webdriver.Chrome] = None
    options: Optional[Options] = None
    download_path: Optional[Path] = None

    def __post_init__(self):
        self.build_webdriver()

    def build_webdriver(self, browser_download_path: Optional[Path] = None) -> [webdriver.Chrome, Options, Path]:
        if not (_download_path := self.config.user_options.get('DOWNLOAD_FOLDER')):
            self.config.set_download_path()

        _options = Options()
        # Set the default download directory
        _prefs = {'download.default_directory': str(_download_path)}
        _options.add_experimental_option('prefs', _prefs)
        # _options.add_argument("--headless=True")  # hide GUI
        _options.add_argument("--window-size=1920,1080")  # set window size to native GUI size
        _options.add_argument("start-maximized")  # ensure window is full-screen

        # Set up a webdriver instance
        self.driver = webdriver.Chrome(options=_options)
        self.options = _options
        return self.driver

    def select_election_type(self,
                             driver: Optional[webdriver.Chrome] = None,
                             options: Optional[Options] = None,
                             download_path: Optional[Path] = None) -> [Path, webdriver.Chrome]:
        if not driver:
            driver = self.driver

        if not download_path:
            download_path = self.config.user_options.get('DOWNLOAD_FOLDER')

        if not options:
            options = self.options

        config = self.config.config

        driver.get(config['ELECTION_PICKER_URL'])
        sleep(3)

        """ Select the election type."""
        election_type_dropdown = Select(driver.find_elements(
            By.ID,
            value=config['SELECTION_CLASSES']['ELECTION_LIST_SELECTION'])[0])

        election_list = {option: election.text for option, election in enumerate(election_type_dropdown.options)}
        for option, election in election_list.items():
            if election != "-- Select Election --":
                print(f"{option}: {election}")

        select_election = input("Select an election: ")
        choice = election_list.get(int(select_election))
        ic("Selected election type: ", choice)

        """ Create folders for the election and year."""
        _election_year = choice.split()[0]
        download_path = download_path.joinpath(str(_election_year))  # Create folder for year
        download_path.mkdir(parents=True, exist_ok=True)
        download_path = download_path.joinpath(choice)  # Create folder for election
        download_path.mkdir(parents=True, exist_ok=True)
        ic("Select Election Types Func: ", download_path)
        options.add_experimental_option('prefs', {'download.default_directory': str(download_path)})
        driver.options = options
        if self.config.save_options:
            (USER_OPTIONS := self.config.user_options)[str(_election_year)] = self.config.user_options.get(str(_election_year), [])
            if choice not in USER_OPTIONS[str(_election_year)]:
                USER_OPTIONS[str(_election_year)].append(choice)

        """ Load selected election choice."""
        ic("Selected election type: ", choice)
        election_type_dropdown.select_by_visible_text(choice)
        sleep(2)
        submit_button = driver.find_elements(By.XPATH, value=config['BUTTONS']['SUBMIT'])[0]
        submit_button.click()

        self.download_path = download_path
        self.driver = driver
        return self

    def export_early_vote_lists(
            self,
            max_delay: int = 15):
        OBJECT_LOOKER = []
        driver = self.driver
        config = self.config.config
        folder_to_download_to = self.config.user_options.get('DOWNLOAD_FOLDER')
        new_folder_path = self.download_path
        ic("Folder to download to: ", folder_to_download_to)
        for date_to_vote in [
            config['ELECTION_DAY_SELECTION']['EARLY_VOTE'],
            config['ELECTION_DAY_SELECTION']['ELECTION_DAY']]:
            try:
                _select_election_dropdown = Select(driver.find_element(By.ID, value=date_to_vote))
                _vote_dates = iter([x.text for x in _select_election_dropdown.options])
                ic("Selected election vote dates: ", _vote_dates)
                _previous_day_totals = None

                for _date in _vote_dates:
                    try:
                        _date_format = (
                            datetime.strptime(
                                _date,
                                config['FORMATTING']['SOS_DATE']
                            ).strftime(
                                config['FORMATTING']['NEW_DATE']
                            )
                        )

                        _dropdown = Select(driver.find_element(By.ID, value=date_to_vote))
                        ic("Selected date dropdown: ", _date)
                        _dropdown.select_by_visible_text(_date)
                        ic("Selected date select visible text: ", _date)
                        _submit = driver.find_element(By.XPATH, value=config['BUTTONS']['SUBMIT'])
                        _submit.click()
                        sleep(.5)
                        _county_turnout_table = driver.find_elements(
                            By.XPATH,
                            value=config['XPATHS']['DAILY_TOTALS'])

                        OBJECT_LOOKER.append(_county_turnout_table)
                        _county_turnout_table = _county_turnout_table[0].text.split("\n")  # Split the table into rows
                        # TODO: Create a table so it shows each county turnout by each day, and add it to a list.
                        if len(_county_turnout_table) != 1:
                            _total_row = _county_turnout_table[-1].split()  # Get the last row
                            if _total_row[0] == "TOTAL":
                                # Get the total number of voters for the day
                                _day_totals = int(_total_row[-2].replace(",", ""))  # Remove commas and convert to int
                            else:
                                raise ValueError("Total row not found.")
                            ic(f"{_date} totals: {_day_totals:,}")
                        else:
                            _day_totals = _previous_day_totals

                        if _day_totals == _previous_day_totals:
                            # If no new data, go back to previous day instead of trying to generate a report
                            ic("Day totals are the same, going back to previous day.")
                            _go_back = driver.find_element(
                                By.XPATH, value=config['BUTTONS']['PREVIOUS'])
                            _go_back.click()

                        else:
                            _generate_report_button = driver.find_element(
                                By.XPATH,
                                value=config['BUTTONS']['GENERATE_REPORT']
                            )
                            _generate_report_button.click()
                            sleep(3)
                            _previous_day_totals = _day_totals
                            ic("Updated previous day totals: ", f"{_previous_day_totals:,}")
                            _go_back = driver.find_element(By.XPATH, value=config['BUTTONS']['PREVIOUS'])
                            _go_back.click()

                        # TODO: Fix this so that it downloads to the correct folder (may need to check download path below)
                        # Get list of files
                        _files = [f for f in folder_to_download_to.glob('*') if f.is_file()]

                        # Find downloaded file
                        _most_recent_file = max(_files, key=lambda file: file.stat().st_mtime)
                        ic("Most recent file: ", _most_recent_file)
                        check_count = 0
                        while True:
                            try:
                                if Path(_most_recent_file).suffix in [".crdownload", ".csv.crdownload"]:
                                    check_count += 1
                                    ic(f"File is still downloading, waiting {check_count}...")
                                    sleep(5)
                                    _most_recent_file = max(_files, key=lambda file: file.stat().st_mtime)
                                else:
                                    ic("File is done downloading.")
                                    check_count = 0
                                    break
                            except FileNotFoundError:
                                sleep(1)

                        if date_to_vote == config['ELECTION_DAY_SELECTION']['ELECTION_DAY']:
                            # Move the zip file to the respective folder
                            zip_file_path = new_folder_path / f"{_date_format}.zip"
                            shutil.move(_most_recent_file, zip_file_path)

                            # Unzip the file
                            with zipfile.ZipFile(zip_file_path, 'r') as zip_ref:
                                zip_ref.extractall(folder_to_download_to)

                            # Find the .csv file with 'VOTER' in the name and move it to the previous folder
                            voter_csv_file = glob.glob(f"{folder_to_download_to}/*VOTER*.csv")[0]
                            shutil.move(voter_csv_file, new_folder_path / f"{_date_format}.csv")
                        else:
                            # Rename and move file
                            voter_csv_file = glob.glob(f"{folder_to_download_to}/*STATE*.csv")
                            if voter_csv_file:
                                Path(voter_csv_file[0]).unlink()

                            shutil.move(_most_recent_file, new_folder_path / f"{_date_format}.csv")
                            ic(f"Moved file to: {new_folder_path.stem}/{_date_format}.csv")
                    except ValueError:
                        pass
                    except StopIteration:
                        break

                # _go_back = driver.find_element(By.XPATH, value=config['BUTTONS']['PREVIOUS'])
                # _go_back.click()
            except ValueError:
                pass
            except (StopIteration
                    or selenium.common.exceptions.UnexpectedTagNameException
                    or selenium.common.exceptions.NoSuchElementException):
                driver.quit()

            print("Done!")


if __name__ == "__main__":
    setup = CreateScraper()
    setup.select_election_type()
    setup.export_early_vote_lists()

    # INITIAL_PATH = set_download_path()
    # test_driver, options, INITIAL_PATH = build_webdriver(INITIAL_PATH)
    # DOWNLOAD_PATH, driver = select_election_type(test_driver, options, INITIAL_PATH)
    # export_early_vote_lists(folder_to_download_to=INITIAL_PATH,
    #                         new_folder_path=DOWNLOAD_PATH,
    #                         driver=test_driver)
    # election_data = ReadElectionData(folder=DOWNLOAD_PATH)
    # election_data.read_files()




# export_early_vote_lists(
#     select_election("2024", "Republican Primary")
# )
#
# export_early_vote_lists(
#     select_election("2024", "Democratic Primary")
# )


