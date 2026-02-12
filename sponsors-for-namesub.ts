#!/usr/bin/env bun

import {ALPHA_SPONSORS, BETA_SPONSORS, GAMMA_SPONSORS, PARTNERS, MEDIA_PARTNERS, Podkrepqsht} from "../ht-web/app/_configs/podkrepq";

function getGoodName(name: Podkrepqsht["name"]) {
    if (name === "Sofia Tech Park") return "СОФИЯ ТЕХ ПАРК";
    if (name === "Bosch Engineering Center Sofia") return "Bosch Engineering";
    return name;
}

const sponsorStr = (paket: string) => (sponsor: Podkrepqsht) => `${getGoodName(sponsor.name)}\t${paket}`;

const sponsors = [
    ...ALPHA_SPONSORS.map(sponsorStr("Алфа Спонсор")),
    ...BETA_SPONSORS.map(sponsorStr("Бета Спонсор")),
    ...GAMMA_SPONSORS.map(sponsorStr("Гама Спонсор")),
    ...PARTNERS.map(sponsorStr("Партньор")),
    ...MEDIA_PARTNERS.map(sponsorStr("Медиен Партньор")),
];

console.log(sponsors.join("\n"));