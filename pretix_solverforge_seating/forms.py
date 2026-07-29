from __future__ import annotations

import json
import re
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _
from pretix.base.models import Question

from .models import PlannerConfiguration


class PlannerConfigurationForm(forms.ModelForm):
    accessible_seats = forms.CharField(
        required=False,
        label=_("Accessible seat IDs"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("One pretix seat ID per line, or a comma-separated list."),
    )
    aisle_seats = forms.CharField(
        required=False,
        label=_("Aisle seat IDs"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("Mark seats adjacent to an aisle; use pretix seat IDs."),
    )
    zone_quality_json = forms.CharField(
        required=False,
        label=_("Zone quality"),
        widget=forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
        help_text=_('JSON object with integer values from 0 to 100, e.g. {"Stalls": 90}.'),
    )

    class Meta:
        model = PlannerConfiguration
        fields = (
            "wheelchair_question_identifier",
            "companion_question_identifier",
            "preference_question_identifier",
            "accessible_seats",
            "aisle_seats",
            "zone_quality_json",
            "random_seed",
            "step_count_limit",
        )
        labels = {
            "wheelchair_question_identifier": _("Wheelchair question"),
            "companion_question_identifier": _("Companion question"),
            "preference_question_identifier": _("Seat preference question"),
            "random_seed": _("Deterministic random seed"),
            "step_count_limit": _("Local-search step limit"),
        }
        help_texts = {
            "wheelchair_question_identifier": _(
                "Each truthy order-position answer requires one accessible seat "
                "assigned to that position."
            ),
            "preference_question_identifier": _(
                "Supported answer/option identifiers are front, rear, aisle, "
                "and zone-ZONE-SLUG (for example, zone-main-balcony)."
            ),
            "companion_question_identifier": _(
                "A truthy answer requires a party of at least two containing an "
                "accessible seat."
            ),
        }

    def __init__(self, *args: Any, event: object, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        questions = list(
            Question.objects.filter(event=event)
            .exclude(identifier="")
            .order_by("position", "pk")
        )
        choices = [("", _("Not configured"))] + [
            (question.identifier, f"{question.identifier} — {question.question}")
            for question in questions
        ]
        for field_name in (
            "wheelchair_question_identifier",
            "companion_question_identifier",
            "preference_question_identifier",
        ):
            current = str(getattr(self.instance, field_name, "") or "")
            field_choices = list(choices)
            if current and current not in {value for value, _label in field_choices}:
                field_choices.append(
                    (
                        current,
                        _("%(identifier)s (missing)") % {"identifier": current},
                    )
                )
            self.fields[field_name] = forms.ChoiceField(
                choices=field_choices,
                required=False,
                label=self._meta.labels[field_name],
                help_text=self._meta.help_texts.get(field_name),
            )
        self.fields["accessible_seats"].initial = "\n".join(
            str(value) for value in self.instance.accessible_seat_guids
        )
        self.fields["aisle_seats"].initial = "\n".join(
            str(value) for value in self.instance.aisle_seat_guids
        )
        self.fields["zone_quality_json"].initial = json.dumps(
            self.instance.zone_quality,
            indent=2,
            sort_keys=True,
        )

    def clean_accessible_seats(self) -> list[str]:
        return _guid_list(self.cleaned_data["accessible_seats"])

    def clean_aisle_seats(self) -> list[str]:
        return _guid_list(self.cleaned_data["aisle_seats"])

    def clean_zone_quality_json(self) -> dict[str, int]:
        raw = self.cleaned_data["zone_quality_json"].strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(
                _("Enter valid JSON: %(error)s"),
                params={"error": exc.msg},
            ) from exc
        if not isinstance(value, dict):
            raise forms.ValidationError(_("Zone quality must be a JSON object."))
        result: dict[str, int] = {}
        for zone, quality in value.items():
            if not isinstance(zone, str) or not zone.strip():
                raise forms.ValidationError(_("Every zone name must be a non-empty string."))
            if isinstance(quality, bool) or not isinstance(quality, int):
                raise forms.ValidationError(
                    _("Quality for %(zone)s must be an integer."),
                    params={"zone": zone},
                )
            if not 0 <= quality <= 100:
                raise forms.ValidationError(
                    _("Quality for %(zone)s must be between 0 and 100."),
                    params={"zone": zone},
                )
            result[zone.strip()] = quality
        return result

    def clean_step_count_limit(self) -> int:
        value = int(self.cleaned_data["step_count_limit"])
        if not 1 <= value <= 100_000:
            raise forms.ValidationError(_("Choose a value from 1 to 100000."))
        return value

    def save(self, commit: bool = True) -> PlannerConfiguration:
        self.instance.accessible_seat_guids = self.cleaned_data["accessible_seats"]
        self.instance.aisle_seat_guids = self.cleaned_data["aisle_seats"]
        self.instance.zone_quality = self.cleaned_data["zone_quality_json"]
        return super().save(commit=commit)


class CommitConfirmationForm(forms.Form):
    confirm = forms.BooleanField(
        label=_("I understand that this writes the proposed seats to real order positions."),
        required=True,
    )


def _guid_list(raw: str) -> list[str]:
    return sorted(
        {
            token.strip()
            for token in re.split(r"[\s,;]+", raw)
            if token.strip()
        }
    )
