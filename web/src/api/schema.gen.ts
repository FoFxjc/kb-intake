// Generated from contract/api/openapi-v1.json by scripts/gen-api.mjs (openapi-typescript). Do not edit.

export interface paths {
    "/v1/directories": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Directories */
        get: operations["directories_v1_directories_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/engines": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Engines Info */
        get: operations["engines_info_v1_engines_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["health_v1_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Jobs */
        get: operations["list_jobs_v1_jobs_get"];
        put?: never;
        /** Create Job */
        post: operations["create_job_v1_jobs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Job Detail */
        get: operations["job_detail_v1_jobs__job_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/approve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Approve */
        post: operations["approve_v1_jobs__job_id__approve_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/clusters": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Job Clusters
         * @description Groups of similar regions (two or more members each); a group changes no data.
         */
        get: operations["job_clusters_v1_jobs__job_id__clusters_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/confirm": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Confirm */
        post: operations["confirm_v1_jobs__job_id__confirm_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Job Events */
        get: operations["job_events_v1_jobs__job_id__events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/facets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Facets
         * @description Counts per type, status and open gap code for the same filters as /regions; each dimension is counted
         *     without its own filter.
         */
        get: operations["facets_v1_jobs__job_id__facets_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/gaps": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Job Gaps */
        get: operations["job_gaps_v1_jobs__job_id__gaps_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/llm": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Job Llm
         * @description LLM outputs of the job (llm.jsonl rows) and progress per use.
         */
        get: operations["job_llm_v1_jobs__job_id__llm_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/pages/{n}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Page */
        get: operations["page_v1_jobs__job_id__pages__n__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/pages/{n}/image": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Page Image */
        get: operations["page_image_v1_jobs__job_id__pages__n__image_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/queue": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Job Queue
         * @description The review inbox: gaps and weak continuation hints grouped by problem, each open, seen or resolved (empty
         *     before extraction).
         */
        get: operations["job_queue_v1_jobs__job_id__queue_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/queue/seen": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Queue Seen
         * @description Mark queue items as shown to the reviewer (work state only; never delivered).
         */
        post: operations["queue_seen_v1_jobs__job_id__queue_seen_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/regions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Regions
         * @description Regions in reading order: label-stage before extraction, current after. `q` searches region text, table
         *     cells and LLM descriptions (every word as a prefix); repeat `type`, `status`, `gap` for several values.
         */
        get: operations["regions_v1_jobs__job_id__regions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry */
        post: operations["retry_v1_jobs__job_id__retry_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Review List */
        get: operations["review_list_v1_jobs__job_id__review_get"];
        put?: never;
        /** Review Ops */
        post: operations["review_ops_v1_jobs__job_id__review_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/source.pdf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Source Pdf
         * @description The job's source file, with HTTP Range (PDF.js loads pages on demand).
         */
        get: operations["source_pdf_v1_jobs__job_id__source_pdf_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Job Summary
         * @description The document summary (LLM, low weight); `off` when the LLM is not enabled.
         */
        get: operations["job_summary_v1_jobs__job_id__summary_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/jobs/{job_id}/workflow": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Workflow */
        get: operations["get_workflow_v1_jobs__job_id__workflow_get"];
        /** Put Workflow */
        put: operations["put_workflow_v1_jobs__job_id__workflow_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/packages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Packages */
        get: operations["packages_v1_packages_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AckOp */
        AckOp: {
            /**
             * Action
             * @constant
             */
            action: "ack";
            /**
             * Item Id
             * @description a queue item (GET /queue)
             */
            item_id: string;
            /**
             * Note
             * @default null
             */
            note: string | null;
            /**
             * Verdict
             * @default ok
             * @enum {string}
             */
            verdict: "ok" | "wrong";
        };
        /** ApproveBody */
        ApproveBody: {
            /** By */
            by: string;
            /**
             * Version
             * @default null
             */
            version: number | null;
        };
        /** Cell */
        Cell: {
            /** Bbox */
            bbox: number[] | null;
            /** Bbox Source */
            bbox_source?: string | null;
            /** C */
            c: number;
            /** Cs */
            cs: number;
            /** Is Header */
            is_header: boolean;
            /** R */
            r: number;
            /** Rs */
            rs: number;
            /** Text */
            text: string | null;
        } & {
            [key: string]: unknown;
        };
        /** Cluster */
        Cluster: {
            /**
             * Basis
             * @description the values the members share (the grouping key)
             */
            basis: {
                [key: string]: unknown;
            };
            /**
             * Cluster Id
             * @description stable for the same members and rule: kind + ':' + 12 hex
             */
            cluster_id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "repeated_text" | "same_shape" | "same_table_header" | "same_gap";
            /** Label */
            label: string;
            /**
             * Members
             * @description region ids in reading order
             */
            members: string[];
            /** N */
            n: number;
            /** Pages */
            pages: number[];
        };
        /** ClusterList */
        ClusterList: {
            /** Clusters */
            clusters: components["schemas"]["Cluster"][];
        };
        /** ConfirmBody */
        ConfirmBody: {
            /** By */
            by: string;
        };
        /** ContinuedHint */
        ContinuedHint: {
            /** Reason */
            reason: string;
            /** Table Id */
            table_id: string;
        };
        /** CreateJobJson */
        CreateJobJson: {
            /**
             * Declaration
             * @description the declaration, YAML text
             */
            declaration: string;
            /**
             * Path
             * @description an absolute path of a PDF inside scan_roots
             */
            path: string;
        };
        /** CreateJobResult */
        CreateJobResult: {
            /** Created */
            created: boolean;
            /** Existing Job */
            existing_job: boolean;
            /** Existing Note */
            existing_note?: string | null;
            /** Job Id */
            job_id: string;
            /** Started */
            started: boolean;
            /** Status */
            status: string;
        };
        /** CreateJobUpload */
        CreateJobUpload: {
            /**
             * Declaration
             * @description the declaration, YAML text
             */
            declaration: string;
            /**
             * File
             * Format: binary
             */
            file: string;
        };
        /** DirectoriesView */
        DirectoriesView: {
            /** Entries */
            entries?: components["schemas"]["DirectoryEntry"][] | null;
            /** Path */
            path?: string | null;
            /** Roots */
            roots: string[];
        };
        /** DirectoryEntry */
        DirectoryEntry: {
            /**
             * Kind
             * @enum {string}
             */
            kind: "directory" | "pdf";
            /** Name */
            name: string;
            /** Path */
            path: string;
        };
        /** EngineStage */
        EngineStage: {
            /** Models */
            models: {
                [key: string]: unknown;
            }[];
            /** Name */
            name: string;
            /** Params */
            params: {
                [key: string]: unknown;
            };
            /** Problems */
            problems: string[];
            /** Ready */
            ready: boolean;
            /** Version */
            version: string;
        };
        /** EnginesView */
        EnginesView: {
            /** Layout Enabled */
            layout_enabled: boolean;
            llm: components["schemas"]["LlmInfo"];
            /** Models Dir */
            models_dir: string;
            /** Pymupdf */
            pymupdf: string;
            /** Ready */
            ready: boolean;
            /** Stages */
            stages: {
                [key: string]: components["schemas"]["EngineStage"];
            };
        };
        /** ErrorBody */
        ErrorBody: {
            /** Detail */
            detail: unknown;
            /** Error */
            error?: string | null;
        };
        /** Event */
        Event: {
            /** At */
            at: string;
            /** Info */
            info: {
                [key: string]: unknown;
            } | null;
            /** Seq */
            seq: number;
            /** Status */
            status: string;
            /** Step */
            step: string;
        };
        /** EventList */
        EventList: {
            /** Events */
            events: components["schemas"]["Event"][];
        };
        /** ExcludeOp */
        ExcludeOp: {
            /**
             * Action
             * @constant
             */
            action: "exclude";
            /**
             * Note
             * @default null
             */
            note: string | null;
            /** Region Id */
            region_id: string;
        };
        /** Facets */
        Facets: {
            /**
             * Gap
             * @description open gap codes, counted with every filter except `gap`
             */
            gap: {
                [key: string]: number;
            };
            /**
             * Status
             * @description counted with every filter except `status`
             */
            status: {
                [key: string]: number;
            };
            /** Total */
            total: number;
            /**
             * Type
             * @description counted with every filter except `type`
             */
            type: {
                [key: string]: number;
            };
        };
        /** Font */
        Font: {
            /** Bold */
            bold: boolean;
            /** Name */
            name: string;
            /** Size */
            size: number;
        };
        /** Gap */
        Gap: {
            /** Code */
            code: string;
            /** Detail */
            detail: string;
            /** Page No */
            page_no: number | null;
            /** Region Id */
            region_id?: string | null;
            resolved?: components["schemas"]["Resolution"] | null;
            /** Stage */
            stage?: ("label" | "extract" | "review") | null;
            /** Table Id */
            table_id?: string | null;
        } & {
            [key: string]: unknown;
        };
        /** GapList */
        GapList: {
            /** Gaps */
            gaps: components["schemas"]["Gap"][];
        };
        /** Health */
        Health: {
            busy: components["schemas"]["StepRef"] | null;
            /** Contract Version */
            contract_version: string;
            last: components["schemas"]["StepRef"] | null;
            llm: components["schemas"]["LlmInfo"];
            /** Ok */
            ok: boolean;
            /** Version */
            version: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** Job */
        Job: {
            /** Approved At */
            approved_at: string | null;
            /** Approved By */
            approved_by: string | null;
            /** Confirmed At */
            confirmed_at: string | null;
            /** Confirmed By */
            confirmed_by: string | null;
            /** Created At */
            created_at: string;
            /** Declaration */
            declaration: {
                [key: string]: unknown;
            };
            /** Dir */
            dir: string;
            /** Doc Id */
            doc_id: string;
            /** Id */
            id: string;
            /** Last Error */
            last_error: string | null;
            /** Package */
            package: string | null;
            /** Running Op */
            running_op: string | null;
            /** Sha256 */
            sha256: string;
            /** Source Name */
            source_name: string;
            /** Status */
            status: string;
            /** Updated At */
            updated_at: string;
            /** Workflow Version */
            workflow_version: number | null;
        };
        /** JobDetail */
        JobDetail: {
            /** Busy */
            busy: boolean;
            /**
             * Events
             * @description the last 50 events
             */
            events: components["schemas"]["Event"][];
            /**
             * Extract
             * @description extract/run.json; null before extraction
             */
            extract: {
                [key: string]: unknown;
            } | null;
            job: components["schemas"]["Job"];
            /**
             * Stats
             * @description label/stats.json; null before labelling
             */
            stats: {
                [key: string]: unknown;
            } | null;
        };
        /** JobList */
        JobList: {
            /** Jobs */
            jobs: components["schemas"]["JobSummary"][];
        };
        /** JobSummary */
        JobSummary: {
            /** Created At */
            created_at: string;
            /** Doc Id */
            doc_id: string;
            /** Job Id */
            job_id: string;
            /** Last Error */
            last_error: string | null;
            /** Package */
            package: string | null;
            /** Running Op */
            running_op: string | null;
            /** Source Name */
            source_name: string;
            /** Status */
            status: string;
            /** Updated At */
            updated_at: string;
        };
        /** LlmCount */
        LlmCount: {
            /** Cached */
            cached: number;
            /** Done */
            done: number;
            /** Failed */
            failed: number;
            /** Total */
            total: number;
        };
        /** LlmFeatures */
        LlmFeatures: {
            /** Chart Table */
            chart_table: boolean;
            /** Describe */
            describe: boolean;
            /** Summary */
            summary: boolean;
        };
        /** LlmInfo */
        LlmInfo: {
            /** Base Url */
            base_url: string;
            /**
             * Configured
             * @description llm.enabled in the configuration
             */
            configured: boolean;
            /**
             * Enabled
             * @description configured and the key's environment variable is set
             */
            enabled: boolean;
            features: components["schemas"]["LlmFeatures"];
            /** Key Present */
            key_present: boolean;
            /** Model */
            model: string;
            /** Provider */
            provider: string;
            /**
             * Remote
             * @description region images and text are sent to an external service
             */
            remote: boolean;
        };
        /**
         * LlmItem
         * @description An llm.jsonl row.
         */
        LlmItem: {
            /** Input Sha256 */
            input_sha256: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "summary" | "describe" | "chart_table";
            /** Model */
            model: string;
            /**
             * Output
             * @description {text} or {table}
             */
            output: {
                [key: string]: unknown;
            };
            /** Prompt Version */
            prompt_version: string;
            /** Provider */
            provider: string;
            /** Region Id */
            region_id: string | null;
            /**
             * Weight
             * @constant
             */
            weight: "low";
        } & {
            [key: string]: unknown;
        };
        /** LlmProgress */
        LlmProgress: {
            chart_table: components["schemas"]["LlmCount"];
            describe: components["schemas"]["LlmCount"];
            summary: components["schemas"]["LlmCount"];
        };
        /** LlmView */
        LlmView: {
            /** Enabled */
            enabled: boolean;
            /** Items */
            items: components["schemas"]["LlmItem"][];
            progress: components["schemas"]["LlmProgress"];
            /** Remote */
            remote: boolean;
        };
        /** Match */
        Match: {
            /**
             * Bbox
             * @description where to highlight: the cell's box for a cell, else the region's box
             */
            bbox: number[];
            /** C */
            c?: number | null;
            /** R */
            r?: number | null;
            /**
             * Snippet
             * @description the matching text with the hits between « and »
             */
            snippet: string;
            /**
             * Source
             * @description what matched: the region's text, a table cell, or an LLM description
             * @enum {string}
             */
            source: "region" | "cell" | "llm";
            /** Table Id */
            table_id?: string | null;
        };
        /** PackageCheck */
        PackageCheck: {
            /** Contract Version */
            contract_version: string;
            /** Counts */
            counts: {
                [key: string]: unknown;
            };
            /** Errors */
            errors: string[];
            /** Ok */
            ok: boolean;
        };
        /** PackageEntry */
        PackageEntry: {
            check: components["schemas"]["PackageCheck"];
            /** Job Id */
            job_id: string | null;
            /** Package */
            package: string;
        };
        /** PackagesView */
        PackagesView: {
            /** Delivery Dir */
            delivery_dir: string;
            /** Packages */
            packages: components["schemas"]["PackageEntry"][];
        };
        /** PageView */
        PageView: {
            /** Excluded */
            excluded: boolean;
            /** Gaps */
            gaps: components["schemas"]["Gap"][];
            /** Height */
            height: number;
            /** Kind */
            kind: string | null;
            /** Page Label */
            page_label: string | null;
            /** Page No */
            page_no: number;
            /** Regions */
            regions: components["schemas"]["Region"][];
            /** Tables */
            tables: components["schemas"]["Table"][];
            /** Width */
            width: number;
        };
        /** QueueGroup */
        QueueGroup: {
            /** Items */
            items: components["schemas"]["QueueItem"][];
            /** Key */
            key: string;
            /** Label */
            label: string;
            /** Open */
            open: number;
            /** Resolved */
            resolved: number;
            /** Seen */
            seen: number;
            /** Total */
            total: number;
        };
        /** QueueItem */
        QueueItem: {
            /**
             * Bbox
             * @description where to look: the gap's own box (find_tables), else the region's or table's box; null for a page-level item
             */
            bbox: number[] | null;
            /** Code */
            code: string | null;
            /** Detail */
            detail: string;
            /** Group */
            group: string;
            /**
             * Item Id
             * @description stable across review; pass to the `ack` op and to /queue/seen
             */
            item_id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "gap" | "hint" | "llm";
            /** Page No */
            page_no: number | null;
            /** Region Id */
            region_id: string | null;
            resolution: components["schemas"]["Resolution"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "open" | "seen" | "resolved";
            /** Table Id */
            table_id: string | null;
            target: components["schemas"]["Target"];
        };
        /** QueueView */
        QueueView: {
            /**
             * Done
             * @description resolved items
             */
            done: number;
            /** Groups */
            groups: components["schemas"]["QueueGroup"][];
            /** Total */
            total: number;
        };
        /** ReclassifyOp */
        ReclassifyOp: {
            /**
             * Action
             * @constant
             */
            action: "reclassify";
            /**
             * Note
             * @default null
             */
            note: string | null;
            /** Region Id */
            region_id: string;
            /** To */
            to: string;
        };
        /**
         * Region
         * @description A region: label-stage (with docling_label, classes; no status yet) or current (contract/v2 region).
         */
        Region: {
            /** Absorbed Into */
            absorbed_into?: string | null;
            /** Bbox */
            bbox: number[];
            /** Classes */
            classes?: unknown[][] | null;
            /** Docling Label */
            docling_label?: string | null;
            font: components["schemas"]["Font"] | null;
            /** Handler */
            handler?: string | null;
            /** Label Source */
            label_source: string;
            /** Layer */
            layer: string;
            /** Page No */
            page_no: number;
            /** Reading Order */
            reading_order: number;
            /** Region Id */
            region_id: string;
            /** Score */
            score: number | null;
            /** Status */
            status?: string | null;
            /** Subtype */
            subtype: string | null;
            /** Table Id */
            table_id?: string | null;
            /** Text */
            text?: string | null;
            /** Text Source */
            text_source?: string | null;
            /** Type */
            type: string;
        } & {
            [key: string]: unknown;
        };
        /** RegionPage */
        RegionPage: {
            /** Items */
            items: components["schemas"]["RegionRow"][];
            /**
             * Next Cursor
             * @description pass as `cursor` for the next page; null on the last page
             */
            next_cursor: string | null;
            /**
             * Total
             * @description regions passing the filters
             */
            total: number;
        };
        /** RegionRow */
        RegionRow: {
            /** Bbox */
            bbox: number[];
            font: components["schemas"]["Font"] | null;
            /**
             * Gaps
             * @description codes of the region's open gaps
             */
            gaps: string[];
            /** Handler */
            handler: string | null;
            /** Label Source */
            label_source: string;
            /** Layer */
            layer: string;
            /**
             * Llm
             * @description kinds of LLM output the region has
             */
            llm: string[];
            match: components["schemas"]["Match"] | null;
            /** Page No */
            page_no: number;
            /** Reading Order */
            reading_order: number;
            /** Region Id */
            region_id: string;
            /** Score */
            score: number | null;
            /**
             * Status
             * @description null before extraction
             */
            status: string | null;
            /** Subtype */
            subtype: string | null;
            /** Table Id */
            table_id: string | null;
            /**
             * Text Preview
             * @description the first 200 characters of the region's text (text by box from the text layer where the region has none)
             */
            text_preview: string | null;
            /** Type */
            type: string;
        };
        /** Resolution */
        Resolution: {
            /** Action */
            action: string;
            /** Review Seq */
            review_seq: number;
        };
        /** ReviewBody */
        ReviewBody: {
            /** By */
            by: string;
            /** Ops */
            ops: (components["schemas"]["ReclassifyOp"] | components["schemas"]["ExcludeOp"] | components["schemas"]["AckOp"])[];
        };
        /** ReviewOps */
        ReviewOps: {
            /**
             * Ops
             * @description the review operations so far, in order (review.jsonl without the final confirm row)
             */
            ops: components["schemas"]["ReviewRow"][];
        };
        /** ReviewResult */
        ReviewResult: {
            /** Applied */
            applied: components["schemas"]["ReviewRow"][];
            /** Status */
            status: string;
        };
        /**
         * ReviewRow
         * @description A review.jsonl row (contract/v2 review).
         */
        ReviewRow: {
            /** Action */
            action: string;
            /** At */
            at: string;
            /** By */
            by: string;
            /** From */
            from: string | null;
            /** Note */
            note: string | null;
            /** Region Id */
            region_id: string | null;
            /** @description ack rows: the queue item answered */
            target?: components["schemas"]["Target"] | null;
            /** To */
            to: string | null;
            /**
             * Verdict
             * @description ack rows: the reviewer's verdict
             */
            verdict?: ("ok" | "wrong") | null;
        } & {
            [key: string]: unknown;
        };
        /** SeenBody */
        SeenBody: {
            /** Item Ids */
            item_ids: string[];
        };
        /** SeenResult */
        SeenResult: {
            /**
             * Updated
             * @description items that were not marked seen before
             */
            updated: number;
        };
        /** Started */
        Started: {
            /** Job Id */
            job_id: string;
            /** Started */
            started: string;
        };
        /** StepRef */
        StepRef: {
            /** Job Id */
            job_id: string;
            /** Step */
            step: string;
        } & {
            [key: string]: unknown;
        };
        /** Summary */
        Summary: {
            /** Model */
            model: string;
            /** Provider */
            provider: string;
            /** Remote */
            remote: boolean;
            /**
             * Status
             * @enum {string}
             */
            status: "off" | "pending" | "done" | "failed";
            /**
             * Text
             * @description 2-4 sentences; low weight, for reference
             */
            text: string | null;
        };
        /** Table */
        Table: {
            /** Bbox */
            bbox: number[];
            /** Cells */
            cells: components["schemas"]["Cell"][];
            /** Checks */
            checks?: {
                [key: string]: unknown;
            } | null;
            continued_hint?: components["schemas"]["ContinuedHint"] | null;
            /** Extract Method */
            extract_method: string;
            /** Markdown */
            markdown: string;
            /** N Cols */
            n_cols: number;
            /** N Rows */
            n_rows: number;
            /** Page No */
            page_no: number;
            /** Region Id */
            region_id: string;
            /** Table Id */
            table_id: string;
        } & {
            [key: string]: unknown;
        };
        /** Target */
        Target: {
            /** Code */
            code: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "gap" | "hint" | "llm";
            /** Page No */
            page_no: number | null;
            /** Region Id */
            region_id: string | null;
            /** Table Id */
            table_id: string | null;
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** WorkflowSaved */
        WorkflowSaved: {
            /** Version */
            version: number;
        };
        /** WorkflowView */
        WorkflowView: {
            /** Approved Version */
            approved_version: number | null;
            /** Created At */
            created_at: string;
            /** Error */
            error: string | null;
            /** Handlers */
            handlers: string[];
            llm: components["schemas"]["LlmInfo"];
            /** Notes */
            notes: unknown[];
            /** Text */
            text: string;
            /** Types */
            types: string[];
            /** Version */
            version: number;
            /**
             * Workflow
             * @description the parsed workflow; null when the text is invalid
             */
            workflow: {
                [key: string]: unknown;
            } | null;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    directories_v1_directories_get: {
        parameters: {
            query?: {
                path?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DirectoriesView"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    engines_info_v1_engines_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EnginesView"];
                };
            };
        };
    };
    health_v1_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Health"];
                };
            };
        };
    };
    list_jobs_v1_jobs_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobList"];
                };
            };
        };
    };
    create_job_v1_jobs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateJobJson"];
                "multipart/form-data": components["schemas"]["CreateJobUpload"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreateJobResult"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unsupported Media Type */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    job_detail_v1_jobs__job_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    approve_v1_jobs__job_id__approve_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApproveBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Started"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unsupported Media Type */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    job_clusters_v1_jobs__job_id__clusters_get: {
        parameters: {
            query?: {
                kind?: ("repeated_text" | "same_shape" | "same_table_header" | "same_gap") | null;
            };
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClusterList"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    confirm_v1_jobs__job_id__confirm_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConfirmBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Started"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unsupported Media Type */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    job_events_v1_jobs__job_id__events_get: {
        parameters: {
            query?: {
                after?: number;
            };
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EventList"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    facets_v1_jobs__job_id__facets_get: {
        parameters: {
            query?: {
                gap?: string[];
                max_score?: number | null;
                min_score?: number | null;
                page?: number | null;
                q?: string;
                status?: string[];
                type?: string[];
            };
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Facets"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    job_gaps_v1_jobs__job_id__gaps_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GapList"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    job_llm_v1_jobs__job_id__llm_get: {
        parameters: {
            query?: {
                kind?: ("summary" | "describe" | "chart_table") | null;
                region_id?: string | null;
            };
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LlmView"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    page_v1_jobs__job_id__pages__n__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
                n: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PageView"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    page_image_v1_jobs__job_id__pages__n__image_get: {
        parameters: {
            query?: {
                dpi?: number;
            };
            header?: never;
            path: {
                job_id: string;
                n: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description the page as PNG */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "image/png": unknown;
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    job_queue_v1_jobs__job_id__queue_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QueueView"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    queue_seen_v1_jobs__job_id__queue_seen_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SeenBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SeenResult"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unsupported Media Type */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    regions_v1_jobs__job_id__regions_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                gap?: string[];
                limit?: number;
                max_score?: number | null;
                min_score?: number | null;
                page?: number | null;
                q?: string;
                status?: string[];
                type?: string[];
            };
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RegionPage"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    retry_v1_jobs__job_id__retry_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Started"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    review_list_v1_jobs__job_id__review_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewOps"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    review_ops_v1_jobs__job_id__review_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReviewBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewResult"];
                };
            };
            /** @description Bad Request */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unsupported Media Type */
            415: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    source_pdf_v1_jobs__job_id__source_pdf_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description the source file */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/pdf": unknown;
                };
            };
            /** @description the requested byte range (HTTP Range) */
            206: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/pdf": unknown;
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description range not satisfiable */
            416: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    job_summary_v1_jobs__job_id__summary_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Summary"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_workflow_v1_jobs__job_id__workflow_get: {
        parameters: {
            query?: {
                version?: number | null;
            };
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkflowView"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    put_workflow_v1_jobs__job_id__workflow_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/yaml": string;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkflowSaved"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorBody"];
                };
            };
        };
    };
    packages_v1_packages_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PackagesView"];
                };
            };
        };
    };
}
