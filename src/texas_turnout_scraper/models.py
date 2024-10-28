from typing import Optional
from pathlib import Path
import re
import csv
from functools import partial

from pydantic.dataclasses import dataclass as pydantic_dataclass

from election_utils.election_history_codes import (
    VoteMethodCodesBase,
    ElectionTypeCodesBase,
    PoliticalPartyCodesBase
)
from election_utils.election_models import ElectionTypeDetailsBase, ElectionVoteMethodBase, ElectionVoteBase

@pydantic_dataclass
class ReadElectionData:
    folder: Path
    election: Optional[ElectionTypeDetailsBase] = None
    _partial_vote_method: Optional[ElectionVoteMethodBase] = None

    def __init__(self):
        self.election = self.setup_election()


    def setup_election(self):
        _election_name = self.folder.name
        _election_date_pattern = r"(\d{4})\s+([A-Z]+)\s+(\d{1,2})(?:ST|ND|RD|TH)?"
        _election_party = None
        match _election_name:
            case _ if 'PRIMARY' in _:
                _election_type = ElectionTypeCodesBase.PRIMARY
                if 'REPUBLICAN' in _:
                    _election_party = PoliticalPartyCodesBase.REPUBLICAN
                elif 'DEMOCRATIC' in _:
                    _election_party = PoliticalPartyCodesBase.DEMOCRATIC
            case _ if 'GENERAL' in _:
                _election_type = ElectionTypeCodesBase.GENERAL
            case _ if 'RUNOFF' in _:
                _election_type = ElectionTypeCodesBase.RUNOFF

        _date_match = re.match(_election_date_pattern, _election_name)
        _year = int(_date_match.group(1))
        _month = _date_match.group(2)
        _day = int(_date_match.group(3))

        _month_num = datetime.strptime(_month, '%B').month

        _formatted_date = datetime(_year, _month_num, _day)

        if _election_party:
            self._partial_vote_method = partial(VoteMethodCodesBase, party=_election_party)

        return ElectionTypeDetailsBase(
            year=_formatted_date.year if _formatted_date else None,
            election_type=_election_type,
            state='TX',
            desc=_election_name,
        )

    def read_files(self):
        for _file in self.folder.iterdir():
            with open(_file, 'r') as f:
                reader = csv.DictReader(f)
                _vote_date = datetime.strptime(_file.stem, '%Y%m%d')
                for row in reader:
                    _vote_method = None
                    _election_id = self.election.id
                    if _method := row.get('VOTING_METHOD'):
                        match _method:
                            case 'MAIL-IN':
                                _vote_method = VoteMethodCodesBase.MAIL_IN
                            case "IN-PERSON":
                                _vote_method = VoteMethodCodesBase.IN_PERSON
                            case _:
                                raise ValueError(f"Invalid voting method: {_method}")
                        _method_model = self._partial_vote_method(
                            election_id=_election_id,
                            vote_date=_vote_date,
                            vote_method=_vote_method
                        )
                        self.election.add_or_update_vote_method(_method_model)

                    if _voter_id := row.get('VOTER_ID'):
                        _voter_model = ElectionVoteBase(
                            id=_voter_id,
                            election_id=_election_id,
                            vote_method_id=_method_model.id,
                        )
                        self.election.add_voter_or_update(_voter_model)
        return self
