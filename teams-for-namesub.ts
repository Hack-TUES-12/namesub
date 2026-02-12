#!/usr/bin/env bun

import {teams} from "../ht-web/app/db/schema";
import {parseArgs} from "util";

const args = parseArgs({
    args: Bun.argv.slice(2), // Remove the first two arguments (bun and the script path)
    options: {
        postgresUrl: {
            type: 'string',
        },
    },
    strict: true,
    allowPositionals: false,
});

if (typeof args.values.postgresUrl !== "string") {
    throw new Error("Missing postgresUrl");
}
process.env.POSTGRES_URL = args.values.postgresUrl;

import {db} from "../ht-web/app/db";
const allTeams = await db.select().from(teams);
allTeams.forEach(team => {
    console.log(`${team.name}\t${team.isAlumni ? "Отбор на завършили" : "Отбор"}`);
})