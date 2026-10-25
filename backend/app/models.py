from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator


class LoginIn(BaseModel):
    code: str = ""
    mot_de_passe: str = ""


class VehiculeIn(BaseModel):
    nom: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    copie_de: int | None = None


class ArticleIn(BaseModel):
    section: str = Field(min_length=1, max_length=120)
    designation: str = Field(min_length=1, max_length=200)
    quantite: int = Field(ge=0, le=9999)
    observation: str = Field(default="", max_length=500)
    perissable: bool = False
    categorie: str = "materiel"
    gtin: str = Field(default="", max_length=14)

    @field_validator("categorie")
    @classmethod
    def _cat(cls, v: str) -> str:
        return v if v in {"medicament", "materiel"} else "materiel"

    @field_validator("gtin")
    @classmethod
    def _gtin(cls, v: str) -> str:
        v = v.strip()
        if v and not v.isdigit():
            raise ValueError("Le code de la boîte (GTIN) ne contient que des chiffres")
        return v


class LotIn(BaseModel):
    peremption: date
    numero_lot: str = Field(default="", max_length=40)
    quantite: int | None = Field(default=None, ge=1, le=9999)
    gtin: str = Field(default="", max_length=14)  # code scanné : mémorisé sur l'article
    remplace: list[int] = Field(default_factory=list, max_length=50)  # lots retirés en même temps (motif « périmé »)

    @field_validator("peremption")
    @classmethod
    def _range(cls, v: date) -> date:
        if not 2000 <= v.year <= 2100:
            raise ValueError("Date de péremption invalide")
        return v


class RetraitIn(BaseModel):
    motif: str = "perime"


class VerificationIn(BaseModel):
    vehicule_id: int
    manquants: list[int] = Field(default_factory=list, max_length=500)  # articles signalés manquants ou incomplets
    note: str = Field(default="", max_length=1000)


class PersonneIn(BaseModel):
    nom: str = Field(min_length=1, max_length=80)
    telephone: str = Field(default="", max_length=30)
    alertes: bool = False
    gestion: bool = False
    actif: bool = True


class ReglagesIn(BaseModel):
    delai_remplacement: int = Field(ge=1, le=365)
    delai_surveillance: int = Field(ge=1, le=730)
    alertes_actives: bool
    alertes_paliers: str = Field(max_length=60)
    alertes_heure: int = Field(ge=0, le=23)
    alertes_rappel_lundi: bool
    alertes_verification: bool
    whatsapp_bot_url: str = Field(default="", max_length=200)
    whatsapp_bot_key: str | None = Field(default=None, max_length=200)  # None : clé inchangée
    whatsapp_groupe: str = Field(default="", max_length=60)
    url_site: str = Field(default="", max_length=200)
    nom_centre: str = Field(default="", max_length=80)
